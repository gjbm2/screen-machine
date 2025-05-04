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
    running_schedulers, important_triggers, active_events
)

# === Storage for global scheduler state ===
scheduler_bp = Blueprint("scheduler_bp", __name__)

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

# === Run scheduler in real time ===
@scheduler_bp.route("/api/schedulers", methods=["GET"])
def api_list_schedulers():
    return jsonify({"running": list_running_schedulers()})

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["GET"])
def api_get_scheduler_log(publish_destination):
    return jsonify({"log": get_scheduler_log(publish_destination)})

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

scheduler_schedules: Dict[str, Dict[str, Any]] = {}  # Store schedules by destination

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

def get_scheduler_log(publish_destination: str) -> List[str]:
    return scheduler_logs.get(publish_destination, [])

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

@scheduler_bp.route("/api/schedulers/all/status", methods=["GET"])
def api_get_all_scheduler_statuses():
    """Get the status of all schedulers at once."""
    statuses = {}
    
    for destination_id in scheduler_states:
        # Get the current state for this destination
        state = scheduler_states.get(destination_id, 'stopped')
        
        # Get the current schedule stack
        schedule_stack = scheduler_schedule_stacks.get(destination_id, [])
        
        # If there's a schedule, get the next action
        next_action = None
        if schedule_stack:
            # Get the current schedule from the top of the stack
            current_schedule = schedule_stack[-1]
            next_action = get_next_scheduled_action(destination_id, current_schedule)
        
        # Add to the response
        statuses[destination_id] = {
            "status": state,
            "is_running": state == "running",
            "is_paused": state == "paused",
            "next_action": next_action
        }
    
    return jsonify({"statuses": statuses})

# --- Variable sharing endpoints ---
@scheduler_bp.route("/api/schedulers/<publish_destination>/exported-vars", methods=["GET"])
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

@scheduler_bp.route("/api/vars-registry", methods=["GET"])
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

@scheduler_bp.route("/api/schedulers/exported-vars/<var_name>", methods=["POST"])
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

@scheduler_bp.route("/api/schedulers/exported-vars/<var_name>", methods=["DELETE"])
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
