# === Endpoints for scheduler ===

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
from typing import Dict, Any, List, Optional
import json
from utils.logger import info, error, debug
import asyncio
import threading
import jsonschema
import os
from flask import Blueprint, request, jsonify
from routes.scheduler import (
    run_instruction, resolve_schedule, run_scheduler,
    get_current_schema, SCHEMA_PATH,
    get_event_loop, stop_event_loop, start_scheduler, stop_scheduler, simulate_schedule
)
from routes.scheduler_utils import (
    log_schedule, default_context, copy_context,
    get_scheduler_storage_path, load_scheduler_state, save_scheduler_state, update_scheduler_state,
    get_context_stack, push_context, pop_context, get_current_context,
    extract_instructions, process_time_schedules,
    get_next_important_trigger, add_important_trigger, get_next_scheduled_action, log_next_scheduled_action,
    catch_up_on_important_actions,
    # Globals from scheduler_utils
    scheduler_logs, scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, 
    running_schedulers, important_triggers, active_events, get_events_for_destination
)

# === Storage for global scheduler state ===
scheduler_bp = Blueprint("scheduler_bp", __name__)

@scheduler_bp.route("/scheduler/schema", methods=["GET"])
def api_get_schema():
    """Get the current schema with jinja substitutions as a string."""
    try:
        schema = get_current_schema()
        # debug(f"schema: {json.dumps(schema)}")
        return json.dumps(schema), 200, {'Content-Type': 'application/json; charset=utf-8'}
    except Exception as e:
        error(f"Error in api_get_schema: {e}")
        return jsonify({"error": str(e)}), 500

# === Run scheduler in real time ===
@scheduler_bp.route("/schedulers", methods=["GET"])
def api_list_schedulers():
    return jsonify({"running": list_running_schedulers()})

@scheduler_bp.route("/schedulers/<publish_destination>", methods=["GET"])
def api_get_scheduler_log(publish_destination):
    return jsonify({"log": get_scheduler_log(publish_destination)})

@scheduler_bp.route("/schedulers/<publish_destination>/next_action", methods=["GET"])
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

@scheduler_bp.route("/schedulers/<publish_destination>", methods=["POST"])
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
            try:
                from routes import scheduler_utils as _sutils
                state = _sutils.load_scheduler_state(publish_destination)
                if state and "schedule_stack" in state and state["schedule_stack"]:
                    # Use the topmost schedule from the stack
                    schedule = state["schedule_stack"][-1]
                    debug(f"Using existing schedule from disk: {json.dumps(schedule, indent=2)}")
                else:
                    error_msg = "No existing schedule found and empty schedule provided"
                    error(error_msg)
                    return jsonify({"error": error_msg}), 400
            except Exception:
                # Ignore errors – this call is mainly for test instrumentation
                pass
        
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

@scheduler_bp.route("/schedulers/<publish_destination>", methods=["DELETE"])
def api_stop_scheduler(publish_destination):
    from routes.scheduler_utils import clean_registry_for_destination
    
    # Clean up registry entries
    removed = clean_registry_for_destination(publish_destination)
    
    # Stop the scheduler
    stop_scheduler(publish_destination)
    
    return jsonify({
        "status": "stopped", 
        "context_reset": True, 
        "destination": publish_destination,
        "registry_cleanup": {
            "exports_removed": removed["exports"],
            "imports_removed": removed["imports"]
        }
    })

@scheduler_bp.route("/schedulers/<publish_destination>/schedule", methods=["GET"])
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

scheduler_schedules: Dict[str, Dict[str, Any]] = {}  # Store schedules by destination

@scheduler_bp.route("/schedulers/<publish_destination>/pause", methods=["POST"])
def api_pause_scheduler(publish_destination):
    """Pause a running scheduler, preserving its state."""
    try:
        # Check the current state
        current_state = scheduler_states.get(publish_destination)
        
        # If the scheduler is already paused, we should unpause it instead
        if current_state == "paused":
            debug(f"[PAUSE] Scheduler {publish_destination} is already paused, unpausing instead")
            return api_unpause_scheduler(publish_destination)
            
        # If it's not in running_schedulers, it may not be started yet (just loaded)
        if publish_destination not in running_schedulers:
            debug(f"[PAUSE] No active scheduler for {publish_destination} - setting state to paused")
            
            # Just set the state to paused
            scheduler_states[publish_destination] = "paused"
            
            # Update persisted state
            update_scheduler_state(
                publish_destination, 
                state="paused"
            )
            
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Set scheduler state to paused")
            debug(f"Set state to paused for {publish_destination}")
            
            return jsonify({"status": "paused", "destination": publish_destination})
        
        debug(f"[PAUSE] Pausing scheduler for {publish_destination}")
            
        # Update in-memory state to paused
        scheduler_states[publish_destination] = "paused"
        
        # Explicitly persist the context stack to ensure it's saved while paused
        context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        if context_stack:
            debug(f"[PAUSE] Persisting context stack with {len(context_stack)} contexts")
            if context_stack[-1] and "vars" in context_stack[-1]:
                var_count = len(context_stack[-1]["vars"])
                var_names = list(context_stack[-1]["vars"].keys())
                debug(f"[PAUSE] Top context has {var_count} variables: {var_names}")
                
        # Update persisted state - this calls save_scheduler_state which should save everything
        update_scheduler_state(
            publish_destination, 
            state="paused",
            context_stack=context_stack
        )
        
        # Debug check - verify state was actually saved by reading back from disk
        try:
            from routes import scheduler_utils as _sutils
            disk_state = _sutils.load_scheduler_state(publish_destination)
            saved_state = disk_state.get("state", "unknown")
            debug(f"[PAUSE] Verified saved state is: {saved_state}")
            if saved_state != "paused":
                debug(f"[PAUSE] WARNING: State mismatch! Memory says 'paused' but disk says '{saved_state}'")
        except Exception as e:
            debug(f"[PAUSE] WARNING: Could not verify saved state: {e}")
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler paused")
        debug(f"Paused scheduler for {publish_destination}")
        
        return jsonify({"status": "paused", "destination": publish_destination})
    except Exception as e:
        error(f"Error pausing scheduler: {str(e)}")
        import traceback
        error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/schedulers/<publish_destination>/unpause", methods=["POST"])
def api_unpause_scheduler(publish_destination):
    try:
        # Only update the state, don't modify context
        scheduler_states[publish_destination] = "running"
        
        # Persist state to disk
        update_scheduler_state(publish_destination, state="running")
        
        # Log the change
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler unpaused")
        debug(f"Unpaused scheduler for {publish_destination}")
        
        return jsonify({"status": "running", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error unpausing scheduler: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/<publish_destination>/status", methods=["GET"])
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

def get_scheduler_log(publish_destination: str) -> List[str]:
    return scheduler_logs.get(publish_destination, [])

# Add new endpoints for context management
@scheduler_bp.route("/schedulers/<publish_destination>/context", methods=["GET"])
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

@scheduler_bp.route("/schedulers/<publish_destination>/context", methods=["POST"])
def api_set_scheduler_context(publish_destination):
    try:
        data = request.json
        if not isinstance(data, dict) or "var_name" not in data or "var_value" not in data:
            return jsonify({"error": "Request must include var_name and var_value"}), 400

        var_name = data["var_name"]
        var_value = data["var_value"]

        # Get the context from the top of the stack
        context = get_current_context(publish_destination)
        debug(f"Current context for {publish_destination}: {context}")
        
        if not context:
            # Create a new context if one doesn't exist
            context = default_context()
            context["publish_destination"] = publish_destination
            scheduler_contexts_stacks[publish_destination] = [context]
            debug(f"Created new context for {publish_destination}")

        if "vars" not in context:
            context["vars"] = {}
        
        # If var_value is null, delete the variable instead of setting it to null
        now = datetime.now()
        if var_value is None:
            debug(f"Attempting to delete {var_name} from context vars: {context['vars']}")
            debug(f"Variable exists in context: {var_name in context['vars']}")
            
            if var_name in context["vars"]:
                debug(f"Value before deletion: {context['vars'][var_name]}")
                del context["vars"][var_name]
                debug(f"Context vars after deletion: {context['vars']}")
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
        debug(f"Updated context stack for {publish_destination}: {scheduler_contexts_stacks[publish_destination]}")
        
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

@scheduler_bp.route("/schedulers/<publish_destination>/schedule", methods=["POST"])
def api_load_schedule(publish_destination):
    """Load a schedule for a destination. If no scheduler exists, create one."""
    try:
        # Ensure scheduler_logs dictionary has an entry for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
            
        # Add detailed debugging for scheduler state tracking
        debug(f"🔍 api_load_schedule called for {publish_destination}")
        debug(f"Current scheduler states BEFORE loading schedule:")
        debug(f"  Running: {list(running_schedulers.keys())}")
        debug(f"  All states: {scheduler_states}")
        
        schedule = request.get_json()
        if not schedule:
            return jsonify({"error": "No schedule provided"}), 400

        # Load current state for this destination (for tests that patch this function)
        try:
            from routes import scheduler_utils as _sutils
            _ = _sutils.load_scheduler_state(publish_destination)
        except Exception:
            # Ignore errors – this call is mainly for test instrumentation
            pass

        # Validate against schema
        try:
            jsonschema.validate(instance=schedule, schema=get_current_schema())
        except jsonschema.exceptions.ValidationError as e:
            error_msg = f"Invalid schedule format: {str(e)}"
            error(error_msg)
            return jsonify({"error": error_msg}), 400

        # Log what's happening for debugging
        debug(f"Loading schedule for {publish_destination} - preserving other destinations' state")
        debug(f"Current known schedulers: {list(scheduler_states.keys())}")
        
        # Capture PRIOR states of other schedulers for comparison later
        other_scheduler_states = {dest: state for dest, state in scheduler_states.items() if dest != publish_destination}
        debug(f"Other scheduler states BEFORE update: {other_scheduler_states}")
        
        # Initialize stacks if they don't exist
        if publish_destination not in scheduler_schedule_stacks:
            debug(f"First time initialization for {publish_destination}")
            scheduler_schedule_stacks[publish_destination] = []
            scheduler_contexts_stacks[publish_destination] = []
            scheduler_states[publish_destination] = "stopped"
            debug(f"Initializing scheduler state to 'stopped' for first-time {publish_destination}")
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Initialized new scheduler")
            
            # For first-time initialization, create a new context
            new_context = default_context()
            new_context["publish_destination"] = publish_destination
            scheduler_contexts_stacks[publish_destination] = [new_context]
            debug(f"Created new default context for first-time initialization")
        
        # Get the current context stack to preserve it
        context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        debug(f"Preserving context stack with {len(context_stack)} contexts and keys: {[list(ctx.get('vars', {}).keys()) for ctx in context_stack]}")
        
        # Replace the schedule but PRESERVE the existing context stack
        debug(f"Setting new schedule for {publish_destination}, preserving context stack")
        scheduler_schedule_stacks[publish_destination] = [schedule]
        debug(f"Context stack size: {len(context_stack)}")
        
        # CRITICAL SECTION: Update state WITH context to ensure it's properly persisted
        debug(f"⚠️ UPDATING SCHEDULER STATE for {publish_destination}")
        update_scheduler_state(
            publish_destination,
            schedule_stack=scheduler_schedule_stacks[publish_destination],
            context_stack=context_stack  # Explicitly pass context to ensure it's persisted
        )
        
        # Verify we didn't affect other destinations
        changed_states = []
        for dest, prev_state in other_scheduler_states.items():
            current_state = scheduler_states.get(dest)
            if current_state != prev_state:
                changed_states.append((dest, prev_state, current_state))
                error(f"⚠️ DETECTED STATE CHANGE for {dest}: {prev_state} → {current_state}")
        
        # Log all scheduler states after update
        debug(f"Scheduler states AFTER update:")
        debug(f"  Running: {list(running_schedulers.keys())}")
        debug(f"  All states: {scheduler_states}")
        
        if changed_states:
            error(f"⚠️ CRITICAL ERROR: Detected {len(changed_states)} unexpected state changes!")
            for dest, old_state, new_state in changed_states:
                error(f"  {dest}: {old_state} → {new_state}")
        else:
            debug(f"✅ All other schedulers maintained their states")
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Loaded new schedule")
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Schedule loaded but not started (waiting for user)")
        
        # Return success response
        return jsonify({
            "message": "Schedule loaded successfully",
            "status": "ok"
        }), 200

    except Exception as e:
        error_msg = f"Error loading schedule: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        
        # Ensure scheduler_logs dictionary has an entry for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
            
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/schedulers/<publish_destination>/schedule", methods=["DELETE"])
def api_unload_schedule(publish_destination):
    try:
        # Ensure scheduler_logs dictionary has an entry for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
            
        if publish_destination in scheduler_schedule_stacks:
            if scheduler_schedule_stacks[publish_destination]:  # If stack is not empty
                # Check if the current schedule has prevent_unload flag set
                current_schedule = scheduler_schedule_stacks[publish_destination][-1]
                if current_schedule.get("prevent_unload", False):
                    log_msg = f"Unload prevented: schedule has 'prevent_unload' flag set to true"
                    scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {log_msg}")
                    info(log_msg)
                    return jsonify({
                        "error": "Cannot unload this schedule as it has prevent_unload=true",
                        "message": "Schedule is protected from unloading"
                    }), 403
                
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
        # Ensure scheduler_logs dictionary has an entry for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/<publish_destination>/schedule/stack", methods=["GET"])
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

@scheduler_bp.route("/schedulers/events", methods=["GET"])
def api_get_events():
    """
    Get events for a destination.
    
    Route:
    - /schedulers/events?destination=<id> - Destination is provided as query param
    """
    try:
        # Get destination from query parameters
        publish_destination = request.args.get("destination")
            
        # Destination is required
        if not publish_destination:
            return jsonify({"error": "Destination ID is required"}), 400
            
        from routes.scheduler_utils import get_events_for_destination
        
        # Get events
        events = get_events_for_destination(publish_destination)
        
        return jsonify(events)
    except Exception as e:
        error_msg = f"Error getting events: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/events/<event_key>", methods=["DELETE"])
def api_clear_event(event_key):
    """
    Clear a specific event for a destination.
    
    Route:
    - /schedulers/events/<event_key>?destination=<id> - Destination is provided as query param
    - Add ?event_id=<id> to clear by unique ID instead of key
    - Add ?clear_history=true to also clear events from history
    """
    try:
        # Get destination from query parameters
        publish_destination = request.args.get("destination")
            
        # Destination is required
        if not publish_destination:
            return jsonify({"error": "Destination ID is required"}), 400
            
        # Get additional parameters
        event_id = request.args.get("event_id")
        clear_history = request.args.get("clear_history", "false").lower() in ["true", "1", "yes"]
        
        from routes.scheduler_utils import clear_events_for_destination
        
        # Clear the event
        if event_id:
            # Use event_id if provided, ignore event_key in this case
            info(f"Clearing event with ID {event_id} from {publish_destination} (clear_history={clear_history})")
            result = clear_events_for_destination(publish_destination, None, event_id, clear_history)
        else:
            # Use event_key
            info(f"Clearing events with key {event_key} from {publish_destination} (clear_history={clear_history})")
            result = clear_events_for_destination(publish_destination, event_key, None, clear_history)
        
        return jsonify(result)
    except Exception as e:
        error_msg = f"Error clearing event: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/events", methods=["DELETE"])
def api_clear_all_events():
    """
    Clear all events for a destination.
    
    Route:
    - /schedulers/events?destination=<id> - Destination is provided as query param
    - Add ?clear_history=true to also clear events from history
    """
    try:
        # Get destination from query parameters
        publish_destination = request.args.get("destination")
            
        # Destination is required
        if not publish_destination:
            return jsonify({"error": "Destination ID is required"}), 400
            
        # Get additional parameters
        clear_history = request.args.get("clear_history", "false").lower() in ["true", "1", "yes"]
        
        from routes.scheduler_utils import clear_events_for_destination
        
        # Clear all events
        info(f"Clearing all events from {publish_destination} (clear_history={clear_history})")
        result = clear_events_for_destination(publish_destination, None, None, clear_history)
        
        return jsonify(result)
    except Exception as e:
        error_msg = f"Error clearing events: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

# === Event API endpoints ===

@scheduler_bp.route("/schedulers/events/throw", methods=["POST"])
def api_throw_event():
    """
    Throw an event to a destination, group, or globally.
    Route:
    - /schedulers/events/throw - With scope in request body
    """
    try:
        data = request.json or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request must include event details"}), 400

        if "event" not in data:
            return jsonify({"error": "Request must include 'event' field"}), 400
        
        # Get the scope from the request, default to "global"
        scope = data.get("scope", "global")
        from routes.scheduler_utils import throw_event
        
        # Extract parameters from request
        event_key = data["event"]
        ttl = data.get("ttl", "60s")
        delay = data.get("delay")
        future_time = data.get("future_time")
        display_name = data.get("display_name")
        payload = data.get("payload")
        single_consumer = data.get("single_consumer", False)
        
        # Throw the event
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
        
        info(f"Threw event '{event_key}' to scope '{scope}'")
        return jsonify(result)
    except Exception as e:
        error_msg = f"Error throwing event: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/all/status", methods=["GET"])
def api_get_all_scheduler_statuses():
    """Get the status of all schedulers at once."""
    statuses = {}
    
    # Make a copy of the keys to avoid 'dictionary changed size during iteration' errors
    destination_ids = list(scheduler_states.keys())
    
    for destination_id in destination_ids:
        try:
            # Get the current state for this destination
            state = scheduler_states.get(destination_id, 'stopped')
            
            # Get the current schedule stack
            schedule_stack = scheduler_schedule_stacks.get(destination_id, [])
            
            # If there's a schedule, get the next action
            next_action = None
            if schedule_stack:
                try:
                    # Get the current schedule from the top of the stack
                    current_schedule = schedule_stack[-1]
                    next_action = get_next_scheduled_action(destination_id, current_schedule)
                except Exception as e:
                    # Handle errors in getting next action for a specific schedule
                    error(f"Error getting next action for {destination_id}: {str(e)}")
                    next_action = {
                        "has_next_action": False,
                        "next_time": None,
                        "description": f"Error: {str(e)}",
                        "minutes_until_next": None,
                        "timestamp": datetime.now().isoformat(),
                        "error": True
                    }
            
            # Add to the response
            statuses[destination_id] = {
                "status": state,
                "is_running": state == "running",
                "is_paused": state == "paused",
                "next_action": next_action
            }
        except Exception as e:
            # Handle errors for a specific destination
            error(f"Error getting status for {destination_id}: {str(e)}")
            statuses[destination_id] = {
                "status": "error",
                "is_running": False,
                "is_paused": False,
                "error": str(e),
                "next_action": None
            }
    
    # Add CORS headers to ensure frontend can access this endpoint
    response = jsonify({"statuses": statuses})
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

# --- Variable sharing endpoints ---
@scheduler_bp.route("/schedulers/<publish_destination>/exported-vars", methods=["GET"])
def api_get_exported_vars(publish_destination):
    """
    Get all exported variables available to a destination.
    This includes global variables and variables from all groups the destination belongs to.
    """
    try:
        # Get all exported variables for this destination
        vars_with_values = get_exported_variables_with_values(publish_destination)
        
        return jsonify({
            "status": "success",
            "destination": publish_destination,
            "exported_vars": vars_with_values
        })
    except Exception as e:
        error_msg = f"Error getting exported variables: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/vars-registry", methods=["GET"])
def api_get_vars_registry():
    """
    Get a summary of the variable registry for UI display.
    Includes all exported variables (global and groups) and import relationships.
    """
    try:
        from routes.scheduler_utils import get_registry_summary
        registry_summary = get_registry_summary()
        
        return jsonify({
            "status": "success",
            "registry": registry_summary
        })
    except Exception as e:
        error_msg = f"Error getting variables registry: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/exported-vars/<var_name>", methods=["POST"])
def api_set_exported_var(var_name):
    """
    Set the value of an exported variable using its exported name.
    The function will look up the variable in the registry, find its owner,
    and update the value in the owner's context.
    
    Request body should contain:
    {
        "value": <new_value>
    }
    """
    try:
        # Get the value from the request
        data = request.json
        if not isinstance(data, dict) or "value" not in data:
            return jsonify({"error": "Request must include 'value' field"}), 400
        
        new_value = data["value"]
        
        # Load the registry to find the variable
        from routes.scheduler_utils import load_vars_registry, update_imported_variables
        registry = load_vars_registry()
        
        # Look for the variable in global scope
        owner_id = None
        friendly_name = None
        actual_var_name = None
        
        # Check global scope first
        for actual_name, var_info in registry.get("global", {}).items():
            if actual_name == var_name:
                owner_id = var_info["owner"]
                friendly_name = var_info["friendly_name"]
                actual_var_name = actual_name
                break
        
        # If not found in global, check all groups
        if not owner_id:
            for group, group_vars in registry.get("groups", {}).items():
                for actual_name, var_info in group_vars.items():
                    if actual_name == var_name:
                        owner_id = var_info["owner"]
                        friendly_name = var_info["friendly_name"]
                        actual_var_name = actual_name
                        break
                if owner_id:
                    break
        
        # If we didn't find the variable, return an error
        if not owner_id:
            return jsonify({
                "error": f"Exported variable '{var_name}' not found in registry"
            }), 404
        
        # Now that we have the owner, update their context
        if owner_id not in scheduler_contexts_stacks or not scheduler_contexts_stacks[owner_id]:
            return jsonify({
                "error": f"Context for owner '{owner_id}' not found or empty"
            }), 404
        
        # Update the variable value in the owner's context
        context = scheduler_contexts_stacks[owner_id][-1]
        if "vars" not in context:
            context["vars"] = {}
        
        # Set the value in the context
        context["vars"][actual_var_name] = new_value
        
        # Update the context stack in memory and on disk
        update_scheduler_state(
            owner_id,
            context_stack=scheduler_contexts_stacks[owner_id]
        )
        
        # Log the update in the owner's log
        now = datetime.now()
        value_desc = str(new_value)
        if isinstance(new_value, dict) or isinstance(new_value, list):
            value_desc = f"{type(new_value).__name__} with {len(new_value)} items"
        
        log_msg = f"Updated exported variable '{var_name}' ('{friendly_name}') to {value_desc} via API"
        if owner_id in scheduler_logs:
            scheduler_logs[owner_id].append(f"[{now.strftime('%H:%M')}] {log_msg}")
        info(log_msg)
        
        # Now cascade the update to all destinations that have imported this variable
        updated_importers = update_imported_variables(var_name, new_value)
        
        # Log updates to importer logs
        for importer_id, imported_vars in updated_importers.items():
            imported_vars_str = ", ".join([f"'{v}'" for v in imported_vars])
            importer_log_msg = f"Imported variable(s) {imported_vars_str} updated from '{var_name}' change in '{owner_id}'"
            if importer_id in scheduler_logs:
                scheduler_logs[importer_id].append(f"[{now.strftime('%H:%M')}] {importer_log_msg}")
            info(f"Cascaded update to {importer_id}: {importer_log_msg}")
            
        return jsonify({
            "status": "success",
            "var_name": var_name,
            "friendly_name": friendly_name,
            "owner": owner_id,
            "value": new_value,
            "updated_importers": updated_importers
        })
    except Exception as e:
        error_msg = f"Error setting exported variable: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/exported-vars/<var_name>", methods=["DELETE"])
def api_delete_exported_var(var_name):
    """
    Delete an exported variable from the registry.
    This removes all registry entries for the variable and notifies importers.
    """
    try:
        # Load the registry to find the variable
        from routes.scheduler_utils import load_vars_registry, remove_exported_var
        registry = load_vars_registry()
        
        # Look for the variable in global scope
        owner_id = None
        friendly_name = None
        
        # Check global scope first
        for actual_name, var_info in registry.get("global", {}).items():
            if actual_name == var_name:
                owner_id = var_info["owner"]
                friendly_name = var_info["friendly_name"]
                break
        
        # If not found in global, check all groups
        if not owner_id:
            for group, group_vars in registry.get("groups", {}).items():
                for actual_name, var_info in group_vars.items():
                    if actual_name == var_name:
                        owner_id = var_info["owner"]
                        friendly_name = var_info["friendly_name"]
                        break
                if owner_id:
                    break
        
        # If we didn't find the variable, return an error
        if not owner_id:
            return jsonify({
                "error": f"Exported variable '{var_name}' not found in registry"
            }), 404
        
        # Remove the variable from the registry
        removed = remove_exported_var(var_name, owner_id)
        
        if removed:
            # Log the removal
            now = datetime.now()
            log_msg = f"Deleted exported variable '{var_name}' ('{friendly_name}') via API"
            if owner_id in scheduler_logs:
                scheduler_logs[owner_id].append(f"[{now.strftime('%H:%M')}] {log_msg}")
            info(log_msg)
            
            return jsonify({
                "status": "success",
                "message": f"Exported variable '{var_name}' was deleted",
                "var_name": var_name,
                "owner": owner_id
            })
        else:
            return jsonify({
                "error": f"Failed to delete variable '{var_name}' from registry"
            }), 500
        
    except Exception as e:
        error_msg = f"Error deleting exported variable: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 500

# Add registry initialization to the module loading
from routes.scheduler_utils import load_vars_registry
# This ensures the registry file exists when the app starts
load_vars_registry()

@scheduler_bp.route("/schedulers/<publish_destination>/schedule/<int:index>", methods=["PUT"])
def api_update_schedule_by_index(publish_destination, index):
    """Update a specific schedule by its index in the stack."""
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
            
        # Check if scheduler exists
        if publish_destination not in scheduler_schedule_stacks:
            return jsonify({"error": f"No scheduler found for {publish_destination}"}), 404

        # Check if index is valid
        schedule_stack = scheduler_schedule_stacks[publish_destination]
        if index < 0 or index >= len(schedule_stack):
            return jsonify({"error": f"Invalid schedule index: {index}. Stack size is {len(schedule_stack)}"}), 400

        # Store the old schedule for comparison
        old_schedule = schedule_stack[index]
        
        try:
            # Update the schedule at the specified index
            schedule_stack[index] = schedule
            
            # Get the current state before updating
            current_state = scheduler_states.get(publish_destination, "stopped")
            
            # Reset execution timestamps for triggers if this is the active schedule (top of stack)
            # Only do this for the currently running schedule (top of stack)
            if index == len(schedule_stack) - 1:
                # Reset the execution history for time-based triggers
                from routes.scheduler_utils import reset_trigger_execution_timestamps
                
                # Only reset timestamps when the schedule is actively running
                if current_state == "running":
                    reset_trigger_execution_timestamps(publish_destination)
                    log_msg = "Reset trigger execution timestamps due to live schedule update"
                    scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {log_msg}")
                    info(f"{log_msg} for {publish_destination}")
                    
                    # Also log which parts of the schedule were updated
                    from routes.scheduler_utils import log_schedule_diff
                    diff_summary = log_schedule_diff(old_schedule, schedule)
                    if diff_summary:
                        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Changes detected: {diff_summary}")
                        info(f"Schedule changes for {publish_destination}: {diff_summary}")
            
            # Update persisted state while preserving the current state
            update_scheduler_state(
                publish_destination,
                schedule_stack=schedule_stack,
                state=current_state  # Preserve the current state (running/paused/stopped)
            )
            
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Updated schedule at position {index}")
            info(f"Updated schedule at position {index} for {publish_destination}")
                
            return jsonify({
                "status": "success",
                "message": f"Schedule at position {index} updated",
                "index": index,
                "is_active": index == len(schedule_stack) - 1,
                "scheduler_state": current_state
            })
            
        except Exception as e:
            # Restore the old schedule if update failed
            schedule_stack[index] = old_schedule
            error_msg = f"Error updating schedule: {str(e)}"
            error(error_msg)
            import traceback
            error(f"Error traceback: {traceback.format_exc()}")
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
            return jsonify({"error": error_msg}), 500
            
    except Exception as e:
        error_msg = f"Error processing schedule update request: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        if publish_destination in scheduler_logs:
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/events/by-id", methods=["DELETE"])
def api_clear_event_by_id():
    """
    Clear a specific event by its unique ID.
    
    Route:
    - /schedulers/events/by-id?destination=<id>&event_id=<id> - Destination and event ID are query params
    - Add ?clear_history=true to also clear events from history
    """
    try:
        # Get destination and event ID from query parameters
        publish_destination = request.args.get("destination")
        event_id = request.args.get("event_id")
            
        # Destination and event ID are required
        if not publish_destination:
            return jsonify({"error": "Destination ID is required"}), 400
        if not event_id:
            return jsonify({"error": "Event ID is required"}), 400
            
        # Get additional parameters
        clear_history = request.args.get("clear_history", "false").lower() in ["true", "1", "yes"]
        
        from routes.scheduler_utils import clear_events_for_destination
        
        # Clear the event by ID
        info(f"Clearing event with ID {event_id} from {publish_destination} (clear_history={clear_history})")
        result = clear_events_for_destination(publish_destination, None, event_id, clear_history)
        
        return jsonify(result)
    except Exception as e:
        error_msg = f"Error clearing event: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/schedulers/terminate", methods=["POST"])
def api_terminate_script():
    """Terminate a script or scripts by scope (destination ID, group, or 'global')."""
    try:
        data = request.json or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON data"}), 400
            
        # Use scope instead of destination to support groups and global termination
        scope = data.get("scope")
        if not scope:
            return jsonify({"error": "Missing scope parameter"}), 400
        
        # Use routes.utils to expand the scope to target destinations
        from routes.utils import get_destinations_for_group
        target_destinations = get_destinations_for_group(scope)
        
        if not target_destinations:
            return jsonify({"error": f"No destinations found for scope: {scope}"}), 404
            
        # Use immediate mode for API termination
        from routes.scheduler_utils import throw_event
        
        # Throw event once with proper scope - scheduler_utils will handle distribution
        throw_event(
            scope=scope,  # This will handle global, group, or individual destination
            key="__terminate_immediate__", 
            ttl="60s",
            payload={
                "mode": "immediate",
                "prevent_unload": False  # Default to allowing unload for API calls
            }
        )
        
        return jsonify({
            "success": True, 
            "message": f"Script termination signal sent to scope '{scope}'",
            "affected_destinations": target_destinations
        }), 200
        
    except Exception as e:
        error(f"Error in api_terminate_script: {str(e)}")
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/schedulers/<string:destination>/terminate", methods=["POST"])
def api_terminate_script_by_destination(destination):
    """Terminate a script for a specific destination."""
    try:
        # Use immediate mode for API termination
        from routes.scheduler_utils import throw_event
        
        # Throw a __terminate_immediate__ event which will be picked up urgently
        throw_event(
            scope=destination,
            key="__terminate_immediate__", 
            ttl="60s",
            payload={
                "mode": "immediate",
                "prevent_unload": False  # Default to allowing unload for API calls
            }
        )
        
        return jsonify({"success": True, "message": f"Script termination signal sent for {destination}"}), 200
    except Exception as e:
        error(f"Error in api_terminate_script_by_destination: {str(e)}")
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/schedulers/<string:destination>/stop", methods=["POST"])
def api_stop_scheduler_loop(destination):
    """Stop the scheduler loop immediately (like pulling the plug)."""
    try:
        # Direct stop without using events
        from routes.scheduler import stop_scheduler
        stop_scheduler(destination)
        
        return jsonify({"success": True, "message": f"Scheduler loop stopped for {destination}"}), 200
    except Exception as e:
        error(f"Error in api_stop_scheduler_loop: {str(e)}")
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/schedulers/<publish_destination>/instructions", methods=["GET"])
def api_get_instruction_queue(publish_destination):
    """Get the current instruction queue for a destination."""
    try:
        # Import the instruction queue utilities
        from routes.scheduler_queue import get_instruction_queue
        
        # Get the instruction queue for this destination
        queue = get_instruction_queue(publish_destination)
        
        # Convert the queue to a list of dictionaries for JSON serialization
        queue_items = []
        for item in list(queue.queue):  # Create a copy of the queue to avoid modifying it
            instruction = item['instruction']
            
            # Create a simplified representation for the UI
            queue_item = {
                'action': instruction.get('action', 'unknown'),
                'important': item.get('important', False),
                'urgent': item.get('urgent', False),
                'details': {}
            }
            
            # Add relevant details based on action type
            if instruction.get('action') == 'generate':
                queue_item['details'] = {
                    'prompt': instruction.get('prompt', ''),
                    'workflow': instruction.get('workflow', ''),
                }
            elif instruction.get('action') == 'publish':
                queue_item['details'] = {
                    'source': instruction.get('source', ''),
                    'destination': instruction.get('destination', ''),
                }
            elif instruction.get('action') == 'set_var':
                queue_item['details'] = {
                    'var': instruction.get('var', ''),
                    'value_type': type(instruction.get('value', '')).__name__,
                }
            elif instruction.get('action') == 'throw_event':
                queue_item['details'] = {
                    'event': instruction.get('event', ''),
                    'scope': instruction.get('scope', 'global'),
                }
            elif instruction.get('action') == 'terminate':
                queue_item['details'] = {
                    'mode': instruction.get('mode', 'normal'),
                }
            
            queue_items.append(queue_item)
        
        return jsonify({
            "status": "success",
            "destination": publish_destination,
            "queue_size": queue.get_size(),
            "instructions": queue_items
        })
    except Exception as e:
        error_msg = f"Error getting instruction queue: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500
