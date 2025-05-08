# === Scheduler Utilities ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union, Tuple
import json
import os
from utils.logger import info, error, debug
import random
import re
import jinja2
import hashlib

# === Global Storage for Scheduler State ===
scheduler_logs: Dict[str, List[str]] = {}
scheduler_schedule_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of schedules by destination
scheduler_contexts_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of contexts by destination
scheduler_states: Dict[str, str] = {}  # Store paused state by destination
important_triggers: Dict[str, List[Dict[str, Any]]] = {}  # Store important triggers by destination
active_events: Dict[str, Dict[str, datetime]] = {}  # Store active events by destination
running_schedulers = {}  # Store running scheduler tasks

# Add this global variable to store the last execution time of each trigger
# Format: {publish_destination: {trigger_id: timestamp}}
last_trigger_executions = {}

# === Additional functions for exported variable registry ===

# Path to the exported variables registry
VARS_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "scheduler", "_vars.json")

def load_vars_registry() -> Dict[str, Any]:
    """Load the exported variables registry from disk."""
    if not os.path.exists(VARS_REGISTRY_PATH):
        # Create default registry structure if it doesn't exist
        registry = {
            "global": {},
            "groups": {},
            "imports": {},  # Track which destinations have imported which variables
            "last_updated": datetime.now().isoformat()
        }
        save_vars_registry(registry)
        return registry
    
    try:
        with open(VARS_REGISTRY_PATH, 'r') as f:
            registry = json.load(f)
            
        # Add imports tracking if it doesn't exist (for backwards compatibility)
        if "imports" not in registry:
            registry["imports"] = {}
            save_vars_registry(registry)
            
        return registry
    except Exception as e:
        from utils.logger import error
        error(f"Error loading variables registry: {str(e)}")
        # Return default registry on error
        return {
            "global": {},
            "groups": {},
            "imports": {},
            "last_updated": datetime.now().isoformat()
        }

def save_vars_registry(registry: Dict[str, Any]) -> None:
    """Save the exported variables registry to disk."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(VARS_REGISTRY_PATH), exist_ok=True)
    
    # Update last updated timestamp
    registry["last_updated"] = datetime.now().isoformat()
    
    try:
        with open(VARS_REGISTRY_PATH, 'w') as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        from utils.logger import error
        error(f"Error saving variables registry: {str(e)}")

def register_exported_var(var_name: str, friendly_name: str, scope: str, 
                          publish_destination: str, timestamp: str) -> None:
    """
    Register an exported variable in the registry.
    
    Args:
        var_name: The name of the variable being exported
        friendly_name: A user-friendly name for the variable
        scope: Either 'global' or a group name
        publish_destination: The ID of the destination that owns the variable
        timestamp: When the variable was exported
    """
    registry = load_vars_registry()
    
    var_info = {
        "friendly_name": friendly_name,
        "owner": publish_destination,
        "timestamp": timestamp
    }
    
    if scope == "global":
        registry["global"][var_name] = var_info
    else:
        if scope not in registry["groups"]:
            registry["groups"][scope] = {}
        registry["groups"][scope][var_name] = var_info
    
    save_vars_registry(registry)

def register_imported_var(var_name: str, imported_as: str, source_dest_id: str, 
                        importing_dest_id: str, timestamp: str) -> None:
    """
    Register when a destination imports a variable.
    
    Args:
        var_name: The name of the variable as it appears in the registry
        imported_as: The name the variable is imported as in the importer's context
        source_dest_id: The ID of the source destination, or a special value like "group:name" or "scope:name"
        importing_dest_id: The ID of the destination importing the variable
        timestamp: When the variable was imported
    """
    registry = load_vars_registry()
    
    # Initialize imports tracking if needed
    if "imports" not in registry:
        registry["imports"] = {}
    
    # Create structure to track which destinations have imported which variables
    if var_name not in registry["imports"]:
        registry["imports"][var_name] = {}
    
    # Parse the source_dest_id to check for special prefixes
    source_info = {}
    
    if source_dest_id.startswith("group:"):
        # It's a group-based import
        group_name = source_dest_id[6:]  # Remove "group:" prefix
        source_info = {
            "source_type": "group",
            "source": group_name
        }
    elif source_dest_id.startswith("scope:"):
        # It's a scope-based import
        scope_name = source_dest_id[6:]  # Remove "scope:" prefix
        source_info = {
            "source_type": "scope", 
            "source": scope_name
        }
    else:
        # It's a direct destination import (original behavior)
        source_info = {
            "source_type": "destination",
            "source": source_dest_id
        }
    
    # Store the import information with additional source type info
    registry["imports"][var_name][importing_dest_id] = {
        "imported_as": imported_as,
        **source_info,  # Include source_type and source fields
        "timestamp": timestamp
    }
    
    save_vars_registry(registry)

def get_var_registry_for_destination(publish_destination: str) -> Dict[str, Any]:
    """
    Get all exported variables available to a destination.
    This includes global variables and variables from groups the destination belongs to.
    
    Args:
        publish_destination: The ID of the destination
    
    Returns:
        Dictionary of variable names to their registry info
    """
    registry = load_vars_registry()
    result = {}
    
    # Add global variables
    for var_name, var_info in registry.get("global", {}).items():
        result[var_name] = var_info
    
    # Find which groups this destination belongs to
    try:
        from routes.utils import _load_json_once
        destinations = _load_json_once("destination", "publish-destinations.json")
        
        # Find the destination's groups
        dest_groups = []
        for dest in destinations:
            if dest["id"] == publish_destination and "groups" in dest:
                dest_groups = dest.get("groups", [])
                break
        
        # Add variables from each group
        for group in dest_groups:
            if group in registry.get("groups", {}):
                for var_name, var_info in registry["groups"][group].items():
                    # Group vars can override global vars
                    result[var_name] = var_info
    
    except Exception as e:
        from utils.logger import error
        error(f"Error getting destination groups: {str(e)}")
    
    return result

def get_exported_variables_with_values(publish_destination: str) -> Dict[str, Any]:
    """
    Get all exported variables available to a destination, 
    with their current values from the owning destination contexts.
    
    Args:
        publish_destination: The ID of the destination
    
    Returns:
        Dictionary of variable names to their values and metadata
    """
    # Get the registry entries for this destination
    registry_entries = get_var_registry_for_destination(publish_destination)
    
    # For each registry entry, get the actual value from the owner's context
    result = {}
    for var_name, var_info in registry_entries.items():
        owner_id = var_info["owner"]
        
        # Get the current value from the owner's context
        value = None
        if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
            owner_context = scheduler_contexts_stacks[owner_id][-1]
            if "vars" in owner_context and var_name in owner_context["vars"]:
                value = owner_context["vars"][var_name]
        
        # Add to result with all metadata plus current value
        result[var_name] = {
            **var_info,
            "value": value
        }
    
    return result

def update_imported_variables(var_name: str, new_value: Any) -> Dict[str, List[str]]:
    """
    Update all contexts that have imported a specific variable.
    
    Args:
        var_name: The name of the variable as it appears in the registry
        new_value: The new value to set
    
    Returns:
        Dictionary mapping destination IDs to list of imported variable names that were updated
    """
    registry = load_vars_registry()
    updates = {}
    
    # If no imports tracked yet, return empty result
    if "imports" not in registry or var_name not in registry["imports"]:
        return updates
    
    # For each destination that imported this variable
    for dest_id, import_info in registry["imports"][var_name].items():
        imported_as = import_info["imported_as"]
        source_type = import_info.get("source_type", "destination")  # Default to destination for backward compatibility
        source = import_info.get("source", import_info.get("source_dest_id", ""))
        
        # Handle the import based on the source_type
        if source_type in ["group", "scope"]:
            # Find the actual variable from the registry
            var_found = False
            
            if source_type == "group" and source in registry.get("groups", {}):
                # Look in the specified group
                if var_name in registry["groups"][source]:
                    var_found = True
            elif source_type == "scope" and source == "global":
                # Look in global scope
                if var_name in registry.get("global", {}):
                    var_found = True
            elif source_type == "scope":
                # Look in the specified scope (which is a group)
                if source in registry.get("groups", {}) and var_name in registry["groups"][source]:
                    var_found = True
            
            # Only update if we found the variable
            if not var_found:
                continue
        
        # Update the destination's context if it exists
        if dest_id in scheduler_contexts_stacks and scheduler_contexts_stacks[dest_id]:
            context = scheduler_contexts_stacks[dest_id][-1]
            if "vars" not in context:
                context["vars"] = {}
            
            # Set the new value
            context["vars"][imported_as] = new_value
            
            # Update the context stack in memory and on disk
            from routes.scheduler_utils import update_scheduler_state
            update_scheduler_state(
                dest_id,
                context_stack=scheduler_contexts_stacks[dest_id],
                force_save=True  # Force save to ensure context changes are persisted
            )
            
            # Track which destinations were updated
            if dest_id not in updates:
                updates[dest_id] = []
            updates[dest_id].append(imported_as)
    
    return updates

# === Logging ===
def log_schedule(message: str, publish_destination: Optional[str] = None, now: Optional[datetime] = None, output: Optional[List[str]] = None):
    """
    Log a message to scheduler logs with timestamp.
    
    Args:
        message: The message to log
        publish_destination: Optional destination to log to. If None, logs to all active destinations.
        now: Optional datetime to use for timestamp. If None, uses current time.
        output: Optional list to append the log message to. If None, logs to console only.
    """
    # Accessing global variables directly
    global scheduler_logs
    
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
    if output:
        output.append(formatted_msg)

# === Context Functions ===
def default_context():
    """Create a new default context."""
    return {
        "vars": {},
        "last_generated": None
    }

def copy_context(context):
    """Create a deep copy of a context."""
    import copy
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
    global scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, scheduler_logs, last_trigger_executions
    
    debug(f"********** LOAD STATE BEGIN: {publish_destination} **********")
    
    # Initialize global variables if they don't exist
    if publish_destination not in scheduler_schedule_stacks:
        scheduler_schedule_stacks[publish_destination] = []
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = []
    if publish_destination not in scheduler_states:
        scheduler_states[publish_destination] = "stopped"
        debug(f"!!!!!!!!!!!!!! STOPPED {publish_destination} - initialization in load_scheduler_state")
        debug(f"********** LOAD STATE: Initialized default state 'stopped' for {publish_destination} **********")
    else:
        debug(f"********** LOAD STATE: Existing memory state is '{scheduler_states[publish_destination]}' for {publish_destination} **********")
    if publish_destination not in scheduler_logs:
        scheduler_logs[publish_destination] = []
    if publish_destination not in last_trigger_executions:
        last_trigger_executions[publish_destination] = {}
    
    path = get_scheduler_storage_path(publish_destination)
    debug(f"********** LOAD STATE: Loading from path: {path} **********")
    
    if os.path.exists(path):
        debug(f"********** LOAD STATE: File exists for {publish_destination} **********")
        try:
            with open(path, 'r') as f:
                file_content = f.read()
                debug(f"********** LOAD STATE: Raw file content for {publish_destination} (first 100 chars): {file_content[:100]}... **********")
                state = json.loads(file_content)
                
            # Validate state structure
            if not isinstance(state, dict):
                debug(f"********** LOAD STATE ERROR: State is not a dictionary for {publish_destination}: {type(state)} **********")
                raise ValueError("Invalid state format: not a dictionary")
                
            # Ensure required fields exist
            if 'state' not in state:
                debug(f"********** LOAD STATE: No 'state' field in file for {publish_destination}, defaulting to 'stopped' **********")
                state['state'] = 'stopped'
                debug(f"!!!!!!!!!!!!!! STOPPED {publish_destination} - default state in loaded file")
            else:
                debug(f"********** LOAD STATE: Found state '{state['state']}' in file for {publish_destination} **********")
            
            if 'context_stack' not in state:
                state['context_stack'] = []
            if 'schedule_stack' not in state:
                state['schedule_stack'] = []
                    
            # Load last trigger executions if available
            if 'last_trigger_executions' in state:
                # Convert string timestamps back to datetime objects
                trigger_executions = {}
                for trigger_id, timestamp_str in state['last_trigger_executions'].items():
                    try:
                        trigger_executions[trigger_id] = datetime.fromisoformat(timestamp_str)
                    except (ValueError, TypeError) as e:
                        error(f"Error parsing trigger execution timestamp: {str(e)}")
                        continue
                
                # Store in the global variable
                last_trigger_executions[publish_destination] = trigger_executions
            else:
                # No saved execution history
                last_trigger_executions[publish_destination] = {}
                
            # Update in-memory state
            debug(f"********** LOAD STATE: Setting in-memory state to '{state.get('state', 'stopped')}' for {publish_destination} **********")
            scheduler_schedule_stacks[publish_destination] = state.get("schedule_stack", [])
            scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
            if state.get('state', 'stopped') == 'stopped':
                debug(f"!!!!!!!!!!!!!! STOPPED {publish_destination} - read from file")
            scheduler_states[publish_destination] = state.get("state", "stopped")
            debug(f"********** LOAD STATE: After update, in-memory state is '{scheduler_states[publish_destination]}' for {publish_destination} **********")
                
            debug(f"********** LOAD STATE SUCCESS: Returning state with '{state.get('state', 'stopped')}' for {publish_destination} **********")
            return state
            
        except json.JSONDecodeError as e:
            debug(f"********** LOAD STATE ERROR: JSON decode error for {publish_destination}: {str(e)} **********")
            error(f"Error decoding scheduler state JSON for {publish_destination}: {str(e)}")
        except Exception as e:
            debug(f"********** LOAD STATE ERROR: Exception for {publish_destination}: {str(e)} **********")
            error(f"Error loading scheduler state for {publish_destination}: {str(e)}")
            import traceback
            error(f"Error traceback: {traceback.format_exc()}")
    else:
        debug(f"********** LOAD STATE: File does not exist for {publish_destination} **********")
    
    # Create a new state with empty stacks
    debug(f"********** LOAD STATE: Creating new default state with 'stopped' for {publish_destination} **********")
    debug(f"!!!!!!!!!!!!!! STOPPED {publish_destination} - creating default state in load_scheduler_state")
    state = {
        "schedule_stack": [],
        "context_stack": [],
        "state": "stopped",
        "last_trigger_executions": {},
        "last_updated": datetime.now().isoformat()
    }
    
    # Save the initial state to disk
    try:
        debug(f"********** LOAD STATE: Saving new default state to disk for {publish_destination} **********")
        save_scheduler_state(publish_destination, state)
    except Exception as e:
        debug(f"********** LOAD STATE ERROR: Failed to save initial state for {publish_destination}: {str(e)} **********")
        error(f"Error saving initial scheduler state for {publish_destination}: {str(e)}")
    
    debug(f"********** LOAD STATE END: Returning new default state for {publish_destination} **********")
    return state

def save_scheduler_state(publish_destination: str, state: Dict[str, Any] = None) -> None:
    """Save the scheduler state to disk for a destination."""
    path = get_scheduler_storage_path(publish_destination)
    debug(f"********** SAVE STATE: Starting for {publish_destination} to {path} **********")
    
    try:
        # Check what state was passed in
        if state is not None:
            passed_in_state = state.get("state", "none-passed")
            debug(f"********** SAVE STATE: State object was passed with state='{passed_in_state}' for {publish_destination} **********")
        else:
            debug(f"********** SAVE STATE: No state object passed for {publish_destination} **********")
        
        # Check what's in memory
        in_memory_state = scheduler_states.get(publish_destination, "not-in-memory")
        debug(f"********** SAVE STATE: Current in-memory state is '{in_memory_state}' for {publish_destination} **********")
    
        # If state is provided, use it (with fallbacks to in-memory state for missing parts)
        # Otherwise use the complete in-memory state
        if state is not None:
            state_to_save = {
                "schedule_stack": state.get("schedule_stack", scheduler_schedule_stacks.get(publish_destination, [])),
                "context_stack": state.get("context_stack", scheduler_contexts_stacks.get(publish_destination, [])),
                "state": state.get("state", scheduler_states.get(publish_destination, "stopped")),
                "last_updated": datetime.now().isoformat(),
                "last_trigger_executions": state.get("last_trigger_executions", {})
            }
        else:
            state_to_save = {
                "schedule_stack": scheduler_schedule_stacks.get(publish_destination, []),
                "context_stack": scheduler_contexts_stacks.get(publish_destination, []),
                "state": scheduler_states.get(publish_destination, "stopped"),
                "last_updated": datetime.now().isoformat(),
                "last_trigger_executions": {}
            }
        
        debug(f"********** SAVE STATE: Will save state='{state_to_save['state']}' for {publish_destination} **********")
        
        # Convert datetime objects in last_trigger_executions to ISO format strings
        if publish_destination in last_trigger_executions:
            for trigger_id, execution_time in last_trigger_executions[publish_destination].items():
                try:
                    if isinstance(execution_time, datetime):
                        state_to_save["last_trigger_executions"][str(trigger_id)] = execution_time.isoformat()
                    else:
                        state_to_save["last_trigger_executions"][str(trigger_id)] = str(execution_time)
                except Exception as e:
                    error(f"Error converting trigger execution time: {e}")
        
        # Log what we're saving
        if state_to_save["context_stack"]:
            context_count = len(state_to_save["context_stack"])
            debug(f"[SAVING STATE] {publish_destination} has {context_count} contexts")
            
            if context_count > 0:
                first_context = state_to_save["context_stack"][0]
                if "vars" in first_context:
                    var_count = len(first_context["vars"])
                    var_names = ", ".join(list(first_context["vars"].keys()))
                    debug(f"[SAVING STATE] First context has {var_count} vars: {var_names}")
        
        # Log the current state we're saving
        debug(f"[SAVING STATE] Current state for {publish_destination} is: {state_to_save['state']}")
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        # Write to a temporary file first for atomic update
        temp_path = path + ".tmp"
        with open(temp_path, 'w') as f:
            json.dump(state_to_save, f, indent=2, default=str)
            f.flush()  # Force flush to disk
        
        # Rename the temporary file to the actual file (atomic operation)
        os.replace(temp_path, path)
        debug(f"********** SAVE STATE: Successfully saved state='{state_to_save['state']}' for {publish_destination} **********")
        
    except Exception as e:
        debug(f"********** SAVE STATE ERROR: Failed to save state for {publish_destination}: {str(e)} **********")
        error(f"CRITICAL ERROR: Failed to save state for {publish_destination}: {str(e)}")
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")

def update_scheduler_state(publish_destination: str, 
                         schedule_stack: Optional[List[Dict[str, Any]]] = None,
                         context_stack: Optional[List[Dict[str, Any]]] = None,
                         state: Optional[str] = None,
                         force_save: bool = False) -> None:
    """Update parts of the scheduler state and then save everything to disk."""
    # Simple, unconditional updating
    debug(f"********** UPDATE STATE: Starting for {publish_destination} **********")
    
    # Check current state before update
    before_state = scheduler_states.get(publish_destination, "not-in-memory")
    debug(f"********** UPDATE STATE: Before update, in-memory state is '{before_state}' for {publish_destination} **********")
    
    # Update in-memory state with any provided values
    if schedule_stack is not None:
        scheduler_schedule_stacks[publish_destination] = schedule_stack
        debug(f"********** UPDATE STATE: Updated schedule_stack: {len(schedule_stack)} items **********")
    
    if context_stack is not None:
        scheduler_contexts_stacks[publish_destination] = context_stack
        debug(f"********** UPDATE STATE: Updated context_stack: {len(context_stack)} contexts **********")
        if context_stack and "vars" in context_stack[-1]:
            debug(f"********** UPDATE STATE: Top context vars: {list(context_stack[-1].get('vars', {}).keys())} **********")
    
    if state is not None:
        debug(f"********** UPDATE STATE: Updating state from '{before_state}' to '{state}' for {publish_destination} **********")
        scheduler_states[publish_destination] = state
    else:
        debug(f"********** UPDATE STATE: No state provided, keeping current state '{before_state}' for {publish_destination} **********")
    
    # Check state after update
    after_state = scheduler_states.get(publish_destination, "not-in-memory")
    debug(f"********** UPDATE STATE: After update, in-memory state is '{after_state}' for {publish_destination} **********")
    
    # Always save the full state to disk
    debug(f"********** UPDATE STATE: Calling save_scheduler_state for {publish_destination} **********")
    
    if force_save:
        # Create a complete state object to force saving everything
        save_state = {
            "schedule_stack": scheduler_schedule_stacks.get(publish_destination, []),
            "context_stack": scheduler_contexts_stacks.get(publish_destination, []),
            "state": scheduler_states.get(publish_destination, "stopped"),
            "last_trigger_executions": {k: v.isoformat() for k, v in last_trigger_executions.get(publish_destination, {}).items()},
            "last_updated": datetime.now().isoformat()
        }
        save_scheduler_state(publish_destination, save_state)
        debug(f"********** UPDATE STATE: Force-saved complete state for {publish_destination} **********")
    else:
        # Use default save behavior
        save_scheduler_state(publish_destination)
    
    debug(f"********** UPDATE STATE: Completed for {publish_destination} **********")

# === Context Stack Management ===
def get_context_stack(publish_destination: str) -> List[Dict[str, Any]]:
    """Get or create the context stack for a destination."""
    global scheduler_contexts_stacks
    
    if publish_destination not in scheduler_contexts_stacks:
        # Try to load from disk first
        state = load_scheduler_state(publish_destination)
        scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
    return scheduler_contexts_stacks[publish_destination]

def push_context(publish_destination: str, context: Dict[str, Any]) -> None:
    """
    Push a new context onto the stack for a destination.
    This preserves the existing schedule stack when updating the state.
    """
    global scheduler_contexts_stacks, scheduler_schedule_stacks
    
    stack = get_context_stack(publish_destination)
    stack.append(context)
    
    # Get current schedule stack to preserve it
    current_schedule_stack = scheduler_schedule_stacks.get(publish_destination, [])
    debug(f"push_context: Preserving existing schedule stack with {len(current_schedule_stack)} items")
    
    # Update state with both context and schedule to avoid losing schedule data
    update_scheduler_state(
        publish_destination,
        context_stack=stack,
        schedule_stack=current_schedule_stack,
        force_save=True  # Force save to ensure context changes are persisted
    )

def pop_context(publish_destination: str) -> Dict[str, Any]:
    """Pop the top context from the stack for a destination."""
    stack = get_context_stack(publish_destination)
    context = stack.pop()
    update_scheduler_state(
        publish_destination,
        context_stack=stack,
        force_save=True  # Force save to ensure context changes are persisted
    )
    return context

def get_current_context(publish_destination: str) -> Optional[Dict[str, Any]]:
    """Get the current context from the top of the stack."""
    stack = get_context_stack(publish_destination)
    return stack[-1] if stack else None

# === Instruction Parsing and Processing ===
def extract_instructions(instruction_container: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract instructions from the new schema's instruction container structure."""
    if not instruction_container:
        return []
        
    # Check if this is an instruction array object with an instructions_block
    if "instructions_block" in instruction_container:
        return instruction_container.get("instructions_block", [])
        
    # Fallback for old format or direct instruction arrays
    return instruction_container.get("instructions", [])

def process_time_schedules(time_schedules: List[Dict[str, Any]], now: datetime, minute_of_day: int, publish_destination: str = None) -> List[Dict[str, Any]]:
    """Process a list of time schedules and return matched schedules without extracting instructions yet."""
    if not time_schedules:
        return []
        
    current_time_str = now.strftime("%H:%M")
    matched_schedules = []
    
    # Initialize last execution tracking for this destination if it doesn't exist
    global last_trigger_executions
    if publish_destination not in last_trigger_executions:
        last_trigger_executions[publish_destination] = {}
    
    for schedule in time_schedules:
        # Generate a stable ID for this schedule for tracking last execution
        # Use a deterministic hash function to ensure stable IDs across restarts
        try:
            # Create a simplified representation of the schedule for hashing
            schedule_key = {
                "time": schedule.get("time", ""),
                "repeat_schedule": schedule.get("repeat_schedule", None)
            }
            schedule_json = json.dumps(schedule_key, sort_keys=True)
            schedule_id = hashlib.md5(schedule_json.encode()).hexdigest()
        except:
            # Fallback to object ID if hashing fails
            schedule_id = str(id(schedule))
        
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
                repeat_interval = repeat_schedule.get("every", "0")
                try:
                    # Always convert to float first
                    repeat_interval = str(repeat_interval).strip()  # Ensure it's a string and remove whitespace
                    repeat_interval = float(repeat_interval)
                    # Convert to int if it's a whole number for display clarity
                    if repeat_interval == int(repeat_interval):
                        repeat_interval = int(repeat_interval)
                except (ValueError, TypeError, AttributeError) as e:
                    error(f"Invalid repeat interval format: {repeat_interval} - {str(e)}")
                    continue
                
                if repeat_interval <= 0:
                    continue
                    
                until_str = repeat_schedule.get("until", "23:59")
                try:
                    until_time = datetime.strptime(until_str, "%H:%M").time()
                    until_minutes = until_time.hour * 60 + until_time.minute
                except ValueError:
                    error(f"Invalid until time format: {until_str}")
                    continue
                
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
                    # Calculate time since start of window (in minutes and seconds for more precision)
                    # Convert everything to seconds for accurate fractional interval calculation
                    start_time = datetime.combine(now.date(), scheduled_time)
                    seconds_since_start = (now - start_time).total_seconds()
                    
                    # Convert repeat_interval to seconds
                    interval_seconds = repeat_interval * 60
                    
                    # Calculate which interval we're in - rounds down to nearest interval
                    current_interval = int(seconds_since_start / interval_seconds)
                    
                    # Calculate the expected execution time for this interval
                    expected_execution_time = start_time + timedelta(seconds=current_interval * interval_seconds)
                    
                    # If we crossed to a previous day, adjust the date
                    if expected_execution_time > now and (now - start_time).total_seconds() < 0:
                        expected_execution_time = datetime.combine(now.date() - timedelta(days=1), expected_execution_time.time())
                    
                    # Calculate the next expected execution time too
                    next_expected_time = start_time + timedelta(seconds=(current_interval + 1) * interval_seconds)
                    
                    # Create a unique identifier for this specific interval
                    interval_id = f"{schedule_id}_{expected_execution_time.isoformat()}"
                    
                    # Debug logging for execution time calculations
                    debug(f"Schedule check: ID={interval_id}, current={now}, " +
                          f"expected_execution={expected_execution_time}, next={next_expected_time}, " +
                          f"interval={repeat_interval}m, seconds_since_start={seconds_since_start}s, " +
                          f"interval_seconds={interval_seconds}s, current_interval={current_interval}")
                    
                    # Only execute if:
                    # 1. This specific interval hasn't been executed yet
                    # 2. We're within 10 seconds of the expected execution time or
                    #    this is the most recent interval and we missed it by less than one interval
                    time_since_expected = (now - expected_execution_time).total_seconds()
                    is_close_to_expected = abs(time_since_expected) < 10  # Within 10 seconds of expected time
                    is_latest_missed = (0 < time_since_expected < interval_seconds)  # We missed it but it's the latest interval
                    
                    if (interval_id not in last_trigger_executions[publish_destination] and
                        (is_close_to_expected or is_latest_missed)):
                        
                        # Record this execution
                        last_trigger_executions[publish_destination][interval_id] = now
                        
                        message = f"Matched repeating time schedule at {current_time_str} (every {repeat_interval} minutes until {until_str})"
                        info(message)
                        if publish_destination:
                            log_schedule(message, publish_destination, now)
                        
                        debug(f"Executing schedule (ID={interval_id}): current={now}, " +
                              f"interval={repeat_interval}m, seconds_since_start={seconds_since_start}s, " +
                              f"expected_time={expected_execution_time}, time_since_expected={time_since_expected}s")
                        
                        matched_schedules.append(schedule)
                    else:
                        if interval_id in last_trigger_executions[publish_destination]:
                            debug(f"Skipping execution (ID={interval_id}): already executed this interval")
                        else:
                            debug(f"Skipping execution (ID={interval_id}): outside execution window, " +
                                  f"now={now}, expected={expected_execution_time}, time_since_expected={time_since_expected}s")
            except (ValueError, TypeError) as e:
                error(f"Error processing repeat schedule: {e}")
                continue
        else:
            # Handle single time point - only execute if current time matches the scheduled time
            # and we haven't executed it already
            if current_time_str == time_str:
                # Create a unique identifier for this one-time trigger
                one_time_id = f"{schedule_id}_{now.date().isoformat()}"
                
                # Check if we've already executed this trigger today
                if one_time_id not in last_trigger_executions[publish_destination]:
                    # Record that we executed this trigger
                    last_trigger_executions[publish_destination][one_time_id] = now
                    
                    message = f"Matched time schedule at {time_str}"
                    info(message)
                    if publish_destination:
                        log_schedule(message, publish_destination, now)
                    matched_schedules.append(schedule)
                else:
                    debug(f"Skipping one-time trigger (ID={one_time_id}) that already executed today: {time_str}")
    
    return matched_schedules

# === Scheduler Support Functions ===
def get_next_important_trigger(publish_destination: str) -> Optional[Dict[str, Any]]:
    """Get the next important trigger for a destination."""
    global important_triggers
    
    if (publish_destination in important_triggers and 
        important_triggers[publish_destination]):
        return important_triggers[publish_destination].pop(0)
    return None

def add_important_trigger(publish_destination: str, trigger: Dict[str, Any], now: datetime):
    """Add an important trigger for a destination."""
    global important_triggers
    
    if publish_destination not in important_triggers:
        important_triggers[publish_destination] = []
    
    # Add timestamp for ordering
    trigger["triggered_at"] = now
    
    important_triggers[publish_destination].append(trigger)

def get_next_scheduled_action(publish_destination: str, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Get the next scheduled action for a destination."""
    try:
        now = datetime.now()
        current_minute = now.hour * 60 + now.minute
        day_str = now.strftime("%A")
        date_str = now.strftime("%-d-%b")
        
        result = {
            "has_next_action": False,
            "next_time": None,
            "description": None,
            "minutes_until_next": float('inf'),
            "timestamp": now.isoformat()
        }
        
        minutes_until_next = float('inf')
        next_action_time = None
        next_action_description = None
        
        # Process each trigger in the schedule
        for trigger in schedule.get("triggers", []):
            # Check trigger type
            if trigger["type"] == "day_of_week" and "days" in trigger:
                # Day of week trigger logic
                current_day_match = day_str in trigger.get("days", [])
                
                # Process each time schedule
                for time_schedule in trigger.get("scheduled_actions", []):
                    if "time" not in time_schedule:
                        continue
                        
                    time_str = time_schedule.get("time")
                    try:
                        scheduled_time = datetime.strptime(time_str, "%H:%M").time()
                        scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                    except ValueError:
                        continue
                    
                    # Check for repeat schedule
                    repeat_schedule = time_schedule.get("repeat_schedule")
                    if repeat_schedule and "every" in repeat_schedule:
                        # Get the interval as a string and convert to float
                        repeat_interval = repeat_schedule.get("every", "0")
                        try:
                            # Always convert to float first
                            repeat_interval = str(repeat_interval).strip()  # Ensure it's a string and remove whitespace
                            repeat_interval = float(repeat_interval)
                            # Convert to int if it's a whole number for display clarity
                            if repeat_interval == int(repeat_interval):
                                repeat_interval = int(repeat_interval)
                        except (ValueError, TypeError, AttributeError) as e:
                            error(f"Invalid repeat interval format: {repeat_interval} - {str(e)}")
                            continue
                            
                        if repeat_interval <= 0:
                            continue
                            
                        # Get the end time
                        until_str = repeat_schedule.get("until", "23:59")
                        try:
                            until_time = datetime.strptime(until_str, "%H:%M").time()
                            until_minutes = until_time.hour * 60 + until_time.minute
                        except ValueError:
                            error(f"Invalid until time format: {until_str}")
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
                        
                        debug(f"Adjusted times - current: {current_minute_adjusted}, scheduled: {scheduled_minutes}, until: {until_minutes}")
                        
                        # If it's before the start time today
                        if current_day_match and current_minute < scheduled_minutes:
                            time_until_next = scheduled_minutes - current_minute
                            if time_until_next < minutes_until_next:
                                minutes_until_next = time_until_next
                                next_action_time = f"{int(scheduled_time.hour):02d}:{int(scheduled_time.minute):02d}"
                                # Format interval differently for fractional minutes
                                if isinstance(repeat_interval, float) and repeat_interval < 1:
                                    # For intervals less than 1 minute, show in seconds
                                    seconds = int(repeat_interval * 60)
                                    next_action_description = f"Repeating '{trigger['type']}' trigger (every {seconds} seconds until {until_str})"
                                else:
                                    # For intervals >= 1 minute, show in minutes with at most 1 decimal place if needed
                                    if repeat_interval == int(repeat_interval):
                                        interval_str = str(int(repeat_interval))
                                    else:
                                        interval_str = f"{repeat_interval:.1f}"
                                    next_action_description = f"Repeating '{trigger['type']}' trigger (every {interval_str} min until {until_str})"
                                debug(f"Found next action before start time: {next_action_time}, {next_action_description}")
                        # If today is the scheduled day and current time is within the repeat window
                        elif current_day_match and scheduled_minutes <= current_minute_adjusted <= until_minutes:
                            # Find next repeat interval
                            current_total_minutes = now.hour * 60 + now.minute + now.second / 60
                            minutes_since_start = current_total_minutes - scheduled_minutes
                            
                            # Calculate the next interval precisely
                            next_interval_decimal = repeat_interval - (minutes_since_start % repeat_interval)
                            if next_interval_decimal == 0 or abs(next_interval_decimal - repeat_interval) < 0.0001:
                                next_interval_decimal = repeat_interval
                            
                            # Store as minutes with decimal precision for calculation
                            next_interval = next_interval_decimal
                            
                            debug(f"Within repeat window - minutes_since_start: {minutes_since_start}, next_interval: {next_interval}")
                            
                            if next_interval < minutes_until_next:
                                minutes_until_next = next_interval
                                
                                # Calculate next time including seconds for fractional intervals
                                next_total_minutes = float(current_minute + next_interval)
                                next_hour = int(next_total_minutes // 60) % 24
                                next_min = int(next_total_minutes % 60)
                                next_action_time = f"{next_hour:02d}:{next_min:02d}"
                                
                                # Format description based on interval size
                                if isinstance(repeat_interval, float) and repeat_interval < 1:
                                    # For intervals less than 1 minute, show in seconds
                                    seconds = int(repeat_interval * 60)
                                    next_action_description = f"Repeating '{trigger['type']}' trigger (every {seconds} seconds until {until_str})"
                                else:
                                    # For intervals >= 1 minute, show in minutes with at most 1 decimal place if needed
                                    if repeat_interval == int(repeat_interval):
                                        interval_str = str(int(repeat_interval))
                                    else:
                                        interval_str = f"{repeat_interval:.1f}"
                                    next_action_description = f"Repeating '{trigger['type']}' trigger (every {interval_str} min until {until_str})"
                                debug(f"Found next action within window: {next_action_time}, {next_action_description}")
                    else:
                        # Single time point
                        # Check if today is the scheduled day and this time is in the future today
                        if current_day_match and scheduled_minutes > current_minute:
                            time_until_next = scheduled_minutes - current_minute
                            if time_until_next < minutes_until_next:
                                minutes_until_next = time_until_next
                                next_action_time = f"{int(scheduled_time.hour):02d}:{int(scheduled_time.minute):02d}"
                                next_action_description = f"'{trigger['type']}' trigger at specific time"
                        # If it's not today or the time has passed, check for future days
                        elif not current_day_match or scheduled_minutes <= current_minute:
                            # Calculate the next occurrence of this day of week
                            days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                            current_day_idx = days_of_week.index(day_str)
                            
                            # Find the next day in the schedule
                            days_until_next = float('inf')
                            for schedule_day in trigger.get("days", []):
                                if schedule_day not in days_of_week:
                                    continue
                                    
                                schedule_day_idx = days_of_week.index(schedule_day)
                                if schedule_day_idx > current_day_idx:
                                    # Next occurrence is later this week
                                    day_diff = schedule_day_idx - current_day_idx
                                elif schedule_day_idx < current_day_idx:
                                    # Next occurrence is next week
                                    day_diff = 7 - (current_day_idx - schedule_day_idx)
                                else:
                                    # Same day, but time has passed
                                    if scheduled_minutes <= current_minute:
                                        day_diff = 7  # Next week
                                    else:
                                        day_diff = 0  # Today
                                        
                                if day_diff < days_until_next:
                                    days_until_next = day_diff
                            
                            if days_until_next < float('inf'):
                                # Calculate minutes until this future event
                                total_minutes = (days_until_next * 24 * 60) + scheduled_minutes - current_minute
                                if total_minutes < 0:
                                    total_minutes += 7 * 24 * 60  # Add a week
                                    
                                if total_minutes < minutes_until_next:
                                    minutes_until_next = total_minutes
                                    next_action_time = f"{int(scheduled_time.hour):02d}:{int(scheduled_time.minute):02d}"
                                    next_action_description = f"'{trigger['type']}' trigger on {days_of_week[(current_day_idx + days_until_next) % 7]}"
            # Day of week trigger ends here
            
            # Date trigger
            elif trigger["type"] == "date" and "date" in trigger:
                # Check if it's today first
                if date_str == trigger.get("date"):
                    # Use same logic as day_of_week for finding next action today
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
                                next_action_time = f"{int(scheduled_time.hour):02d}:{int(scheduled_time.minute):02d}"
                                next_action_description = f"'{trigger['type']}' trigger for today ({date_str})"
                else:
                    # Check for future date
                    try:
                        trigger_date = datetime.strptime(trigger["date"], "%-d-%b").replace(year=now.year)
                        # If date is in the past, it might be for next year
                        if trigger_date.month < now.month or (trigger_date.month == now.month and trigger_date.day < now.day):
                            trigger_date = trigger_date.replace(year=now.year + 1)
                        
                        if trigger_date > now:
                            days_until = (trigger_date - now).days
                            
                            # Use scheduled time if available, otherwise use midnight as default
                            scheduled_minutes_total = 0
                            scheduled_time_str = "00:00"  # Default to midnight
                            
                            # Look for the earliest scheduled time on this date
                            for time_schedule in trigger.get("scheduled_actions", []):
                                if "time" in time_schedule:
                                    time_str = time_schedule.get("time")
                                    try:
                                        scheduled_time = datetime.strptime(time_str, "%H:%M").time()
                                        curr_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                                        if scheduled_minutes_total == 0 or curr_minutes < scheduled_minutes_total:
                                            scheduled_minutes_total = curr_minutes
                                            scheduled_time_str = f"{int(scheduled_time.hour):02d}:{int(scheduled_time.minute):02d}"
                                    except ValueError:
                                        continue
                            
                            # Calculate total minutes until this event
                            minutes_until_date = days_until * 24 * 60 + scheduled_minutes_total
                            
                            if minutes_until_date < minutes_until_next:
                                minutes_until_next = minutes_until_date
                                next_action_time = f"{trigger['date']} {scheduled_time_str}"
                                next_action_description = f"'{trigger['type']}' trigger on {trigger['date']}"
                    except ValueError:
                        continue
        
        # Prepare result if next action found
        if minutes_until_next < float('inf'):
            result["has_next_action"] = True
            result["next_time"] = next_action_time
            result["description"] = next_action_description
            result["minutes_until_next"] = float(minutes_until_next)
            
            # Format the time until next action in a human-friendly way
            if minutes_until_next < 1:
                # For less than a minute, show seconds
                seconds = round(minutes_until_next * 60)  # Round to nearest second
                result["time_until_display"] = f"{seconds} second{'s' if seconds != 1 else ''} from now"
            elif minutes_until_next < 10:
                # For less than 10 minutes, show minutes and seconds for more precision
                minutes_part = int(minutes_until_next)
                seconds_part = round((minutes_until_next - minutes_part) * 60)
                
                if seconds_part == 0:
                    result["time_until_display"] = f"{minutes_part}m from now"
                else:
                    result["time_until_display"] = f"{minutes_part}m {seconds_part}s from now"
            elif minutes_until_next < 60:
                # For 10 minutes to an hour, show rounded minutes
                result["time_until_display"] = f"{round(minutes_until_next)} minutes from now"
            else:
                # For an hour or more, show hours and minutes
                hours = int(minutes_until_next // 60)
                mins = round(minutes_until_next % 60)  # Round to nearest minute
                if mins == 0:
                    result["time_until_display"] = f"{hours}h from now"
                else:
                    result["time_until_display"] = f"{hours}h {mins}m from now"
            
            # Format the description more cleanly for repeating schedules
            if next_action_description and "Repeating" in next_action_description:
                # Extract the interval from the description
                if "seconds" in next_action_description:
                    # Keep seconds as is
                    pass
                elif "min until" in next_action_description:
                    # Clean up minute intervals
                    parts = next_action_description.split("every ")
                    if len(parts) > 1:
                        interval_part = parts[1].split(" min")[0]
                        try:
                            interval = float(interval_part)
                            if abs(interval - round(interval)) < 0.1:  # If close to a whole number
                                interval_str = str(round(interval))
                            else:
                                interval_str = f"{interval:.1f}"
                            result["description"] = f"{parts[0]}every {interval_str} min{' min'.join(parts[1].split(' min')[1:])}"
                        except ValueError:
                            pass  # Keep original if parsing fails
        else:
            # When no action is found, set minutes_until_next to a large numerical value (for JSON compatibility)
            result["minutes_until_next"] = 999999999
        
        return result
    except Exception as e:
        error(f"Error in get_next_scheduled_action: {str(e)}")
        return {
            "has_next_action": False,
            "next_time": None,
            "description": f"Error predicting next action: {str(e)}",
            "minutes_until_next": 999999999,  # Large numerical value for JSON compatibility
            "timestamp": datetime.now().isoformat(),
            "time_until_display": None,
            "error": True
        }

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
    
def catch_up_on_important_actions(publish_destination: str, schedule: Dict[str, Any]):
    """Check for and execute important actions in the current cycle that may have been missed."""
    now = datetime.now()
    current_minute = now.hour * 60 + now.minute
    day_str = now.strftime("%A")
    date_str = now.strftime("%-d-%b")
    
    info(f"[catch_up] Checking for important actions to catch up on at {now.strftime('%H:%M')}")
    context = get_current_context(publish_destination)
    
    # Import here to avoid circular imports
    from routes.scheduler import run_instruction
    
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
                    # Convert string repeat_interval to float
                    if isinstance(repeat_interval, str):
                        try:
                            repeat_interval = float(repeat_interval)
                            # Convert to int if it's a whole number for display clarity
                            if repeat_interval == int(repeat_interval):
                                repeat_interval = int(repeat_interval)
                        except (ValueError, TypeError):
                            error(f"[catch_up] Invalid repeat interval format: {repeat_interval}")
                            continue
                            
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
            # Since the code would be identical, it's omitted here 

def remove_exported_var(var_name: str, publish_destination: str) -> bool:
    """
    Remove an exported variable from the registry.
    
    Args:
        var_name: The name of the variable to remove
        publish_destination: The ID of the destination that owns the variable
    
    Returns:
        bool: True if an entry was removed, False otherwise
    """
    registry = load_vars_registry()
    removed = False
    
    # Check global scope
    if var_name in registry.get("global", {}):
        var_info = registry["global"][var_name]
        if var_info["owner"] == publish_destination:
            del registry["global"][var_name]
            removed = True
    
    # Check all group scopes
    for group_name, group_vars in registry.get("groups", {}).items():
        if var_name in group_vars:
            var_info = group_vars[var_name]
            if var_info["owner"] == publish_destination:
                del group_vars[var_name]
                removed = True
    
    # Also remove any import entries for this variable
    for import_var_name, importers in list(registry.get("imports", {}).items()):
        if import_var_name == var_name:
            # Remove the entire entry for this variable
            if import_var_name in registry["imports"]:
                del registry["imports"][import_var_name]
                removed = True
    
    if removed:
        save_vars_registry(registry)
    
    return removed

def remove_imported_var(var_name: str, importing_dest_id: str) -> bool:
    """
    Remove an imported variable entry from the registry.
    
    Args:
        var_name: The name of the variable that was imported
        importing_dest_id: The ID of the destination that imported the variable
    
    Returns:
        bool: True if an entry was removed, False otherwise
    """
    registry = load_vars_registry()
    removed = False
    
    # Check if this variable has any import entries
    if "imports" in registry and var_name in registry["imports"]:
        # Check if this destination has imported this variable
        if importing_dest_id in registry["imports"][var_name]:
            del registry["imports"][var_name][importing_dest_id]
            removed = True
            
            # If no more importers for this variable, remove the variable entry
            if not registry["imports"][var_name]:
                del registry["imports"][var_name]
    
    if removed:
        save_vars_registry(registry)
    
    return removed

def clean_registry_for_destination(publish_destination: str) -> Dict[str, int]:
    """
    Clean up registry entries for a destination when it's being reset/stopped.
    Removes both exports from this destination and imports by this destination.
    
    Args:
        publish_destination: The ID of the destination being reset
    
    Returns:
        Dict with counts of removed entries
    """
    registry = load_vars_registry()
    removed_counts = {
        "exports": 0,
        "imports": 0
    }
    
    # Check global scope for exports
    for var_name in list(registry.get("global", {}).keys()):
        var_info = registry["global"][var_name]
        if var_info["owner"] == publish_destination:
            del registry["global"][var_name]
            removed_counts["exports"] += 1
    
    # Check all group scopes for exports
    for group_name, group_vars in registry.get("groups", {}).items():
        for var_name in list(group_vars.keys()):
            var_info = group_vars[var_name]
            if var_info["owner"] == publish_destination:
                del group_vars[var_name]
                removed_counts["exports"] += 1
    
    # Check imports
    for var_name, importers in list(registry.get("imports", {}).items()):
        # Remove this destination from importers
        if publish_destination in importers:
            del importers[publish_destination]
            removed_counts["imports"] += 1
            
            # If no more importers for this variable, remove the variable entry
            if not importers:
                del registry["imports"][var_name]
    
    if removed_counts["exports"] > 0 or removed_counts["imports"] > 0:
        save_vars_registry(registry)
    
    return removed_counts

def get_registry_summary() -> Dict[str, Any]:
    """
    Get a summary of the current registry state for UI display.
    
    Returns:
        Dict with global vars, group vars, and import relationships
    """
    registry = load_vars_registry()
    
    # Get current values for exported variables
    global_vars = {}
    for var_name, var_info in registry.get("global", {}).items():
        owner_id = var_info["owner"]
        value = None
        
        # Get current value from owner's context
        if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
            owner_context = scheduler_contexts_stacks[owner_id][-1]
            if "vars" in owner_context and var_name in owner_context["vars"]:
                value = owner_context["vars"][var_name]
        
        global_vars[var_name] = {
            **var_info,
            "value": value
        }
    
    # Get group variables with values
    group_vars = {}
    for group_name, vars_dict in registry.get("groups", {}).items():
        group_vars[group_name] = {}
        for var_name, var_info in vars_dict.items():
            owner_id = var_info["owner"]
            value = None
            
            # Get current value from owner's context
            if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
                owner_context = scheduler_contexts_stacks[owner_id][-1]
                if "vars" in owner_context and var_name in owner_context["vars"]:
                    value = owner_context["vars"][var_name]
            
            group_vars[group_name][var_name] = {
                **var_info,
                "value": value
            }
    
    # Get import relationships
    import_relationships = {}
    for var_name, importers in registry.get("imports", {}).items():
        import_relationships[var_name] = {}
        for importer_id, import_info in importers.items():
            import_relationships[var_name][importer_id] = import_info
    
    return {
        "global": global_vars,
        "groups": group_vars,
        "imports": import_relationships,
        "last_updated": registry.get("last_updated")
    } 

# Add a new utility function to directly set an exported variable
def set_exported_variable(var_name: str, new_value: Any) -> Dict[str, Any]:
    """
    Set the value of an exported variable using its exported name.
    The function will look up the variable in the registry, find its owner,
    and update the value in the owner's context.
    
    Args:
        var_name: The exported variable name in the registry
        new_value: The new value to set
        
    Returns:
        Dictionary with the operation status and details
    """
    # Load the registry to find the variable
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
        return {
            "error": f"Exported variable '{var_name}' not found in registry",
            "status": "error"
        }
    
    # Now that we have the owner, update their context
    if owner_id not in scheduler_contexts_stacks or not scheduler_contexts_stacks[owner_id]:
        return {
            "error": f"Context for owner '{owner_id}' not found or empty",
            "status": "error"
        }
    
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
    
    log_msg = f"Updated exported variable '{var_name}' ('{friendly_name}') to {value_desc}"
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
    
    return {
        "status": "success",
        "var_name": var_name,
        "friendly_name": friendly_name,
        "owner": owner_id,
        "value": new_value,
        "updated_importers": updated_importers
    }

# Similarly, add a function to set a context variable directly
def set_context_variable(publish_destination: str, var_name: str, var_value: Any) -> Dict[str, Any]:
    """
    Set a variable in a destination's context.
    
    Args:
        publish_destination: The destination ID
        var_name: The variable name
        var_value: The value to set
        
    Returns:
        Dictionary with operation status and details
    """
    # Get the context from the top of the stack
    context = get_current_context(publish_destination)
    if not context:
        return {
            "error": "No scheduler context found",
            "status": "error"
        }

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
    
    # Persist changes to disk with force_save to ensure changes are saved
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination],
        force_save=True  # Force save to ensure context changes are persisted
    )
    
    # Check if this is an exported variable that needs to be synchronized
    try:
        # Get the exported variable registry
        registry = load_vars_registry()
        
        # Check if this variable is an exported variable
        is_exported = False
        export_var_name = None
        
        # Check global exports
        for reg_var_name, var_info in registry.get("global", {}).items():
            if var_info.get("owner") == publish_destination and reg_var_name == var_name:
                is_exported = True
                export_var_name = reg_var_name
                break
                
        # Check group exports
        if not is_exported:
            for group_name, group_vars in registry.get("groups", {}).items():
                for reg_var_name, var_info in group_vars.items():
                    if var_info.get("owner") == publish_destination and reg_var_name == var_name:
                        is_exported = True
                        export_var_name = reg_var_name
                        break
                if is_exported:
                    break
        
        # If this is an exported variable, synchronize it to all importers
        if is_exported and export_var_name:
            debug(f"Variable '{var_name}' is exported as '{export_var_name}', updating importers")
            updated_destinations = update_imported_variables(export_var_name, var_value)
            debug(f"Updated imported variables in {len(updated_destinations)} destinations: {updated_destinations}")
    except Exception as e:
        import traceback
        error(f"Error checking for exported variable: {str(e)}\n{traceback.format_exc()}")
    
    return {
        "status": "success", 
        "var_name": var_name, 
        "var_value": var_value,
        "vars": context["vars"],
        "deleted": var_value is None
    }

# === Jinja Template Processing ===
def process_jinja_template(value: Any, context: Dict[str, Any], publish_destination: str = None) -> Any:
    """
    Process Jinja templating in various data types using context variables.
    
    Args:
        value: The value to process - can be string, dict, list, or other types
        context: The current context containing variables
        publish_destination: Optional destination ID for accessing exported variables
        
    Returns:
        The processed value with any Jinja templates substituted
    """
    # If value is not a string and not a container, return as is
    if not isinstance(value, (str, dict, list)):
        return value
        
    # For strings, apply Jinja templating
    if isinstance(value, str):
        # Skip processing if no Jinja template markers
        if '{{' not in value and '{%' not in value:
            return value
            
        try:
            # Set up Jinja environment
            env = jinja2.Environment(
                autoescape=False,  # Don't need HTML escaping
                undefined=jinja2.Undefined  # Allow undefined variables
            )
            template = env.from_string(value)
            
            # Create template variables from context
            template_vars = {}
            
            # Add all variables from context
            if "vars" in context:
                template_vars.update(context["vars"])
            
            # REMOVED: Automatic inclusion of exported variables
            # Variables should only be available if they've been explicitly imported
            # into the context via import_var instruction
                
            # Render the template with our variables
            result = template.render(**template_vars)
            return result
        except Exception as e:
            error(f"Error processing Jinja template: {str(e)}")
            # Return original if there's an error
            return value
    
    # For dictionaries, process each value
    elif isinstance(value, dict):
        result = {}
        for k, v in value.items():
            result[k] = process_jinja_template(v, context, publish_destination)
        return result
    
    # For lists, process each item
    elif isinstance(value, list):
        return [process_jinja_template(item, context, publish_destination) for item in value]
    
    # Default case
    return value

def process_instruction_jinja(instruction: Dict[str, Any], context: Dict[str, Any], publish_destination: str) -> Dict[str, Any]:
    """
    Process an entire instruction object with Jinja templating.
    This applies Jinja templating to ALL string fields in the instruction,
    including parameter names, field names, etc.
    
    Args:
        instruction: The instruction object to process
        context: The current context with variables
        publish_destination: The destination ID for accessing exported variables
        
    Returns:
        A new instruction object with all Jinja templates processed
    """
    # Process the entire instruction using the generic processor
    processed_instruction = process_jinja_template(instruction, context, publish_destination)
    
    # Special handling for certain fields where we need to convert types
    if "action" in processed_instruction:
        action = processed_instruction["action"]
        
        try:
            # Handle duration conversion for wait instruction
            if action == "wait" and "duration" in processed_instruction:
                # Try to convert the duration to a floating-point number if it's a string
                if isinstance(processed_instruction["duration"], str):
                    try:
                        processed_instruction["duration"] = float(processed_instruction["duration"])
                        # Convert to int if it's a whole number for backward compatibility
                        if processed_instruction["duration"] == int(processed_instruction["duration"]):
                            processed_instruction["duration"] = int(processed_instruction["duration"])
                    except (ValueError, TypeError) as e:
                        error(f"Error converting wait duration to number: {processed_instruction['duration']} - {str(e)}")
                        
            # Handle boolean conversions for dontwait property
            if "dontwait" in processed_instruction:
                if isinstance(processed_instruction["dontwait"], str):
                    # Convert string "true"/"false" to boolean
                    if processed_instruction["dontwait"].lower() in ["true", "yes", "1"]:
                        processed_instruction["dontwait"] = True
                    elif processed_instruction["dontwait"].lower() in ["false", "no", "0"]:
                        processed_instruction["dontwait"] = False
                    # If it's some other string, leave as is - the handler will handle it
            
            # Handle repeat_schedule.every conversion to floating-point number
            if action in ["day_of_week", "date"] and "repeat_schedule" in processed_instruction:
                if isinstance(processed_instruction["repeat_schedule"], dict) and "every" in processed_instruction["repeat_schedule"]:
                    if isinstance(processed_instruction["repeat_schedule"]["every"], str):
                        try:
                            # Convert to float first
                            every_value = float(processed_instruction["repeat_schedule"]["every"])
                            # Convert to int if it's a whole number
                            if every_value == int(every_value):
                                every_value = int(every_value)
                            processed_instruction["repeat_schedule"]["every"] = every_value
                        except (ValueError, TypeError) as e:
                            error(f"Error converting repeat interval to number: {processed_instruction['repeat_schedule']['every']} - {str(e)}")
        
        except Exception as e:
            # Log error but continue - we don't want to block execution due to type conversion issues
            error(f"Error during type conversion in Jinja processing: {str(e)}")
    
    return processed_instruction 

def reset_trigger_execution_timestamps(publish_destination: str) -> None:
    """
    Reset execution timestamps for the specified destination's triggers.
    Used when a schedule is updated while running to ensure triggers are properly evaluated.
    
    Args:
        publish_destination: The ID of the destination
    """
    global last_trigger_executions
    
    # Check if we need to initialize the dictionary
    if publish_destination not in last_trigger_executions:
        last_trigger_executions[publish_destination] = {}
    else:
        # Clear existing execution timestamps
        last_trigger_executions[publish_destination] = {}
    
    # Persist the change
    update_scheduler_state(publish_destination)
    
    # Log the reset
    debug(f"Reset trigger execution timestamps for {publish_destination}")

# Helper function for finding exports and imports in actions
def find_exports_imports(actions: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """
    Find export and import variable names in a list of actions.
    
    Args:
        actions: List of action dictionaries
        
    Returns:
        Tuple of (exports, imports) where each is a list of variable names
    """
    exports = []
    imports = []
    
    # Handle case where actions is None
    if not actions:
        return exports, imports
        
    for action in actions:
        # Skip if action is not a dictionary
        if not isinstance(action, dict):
            continue
            
        if action.get("type") == "export_var":
            exports.append(action.get("var_name"))
        elif action.get("type") == "import_var":
            imports.append(action.get("import_as"))
    return exports, imports

def log_schedule_diff(old_schedule: Dict[str, Any], new_schedule: Dict[str, Any]) -> str:
    """
    Generate a summary of differences between old and new schedules.
    
    Args:
        old_schedule: The previous schedule
        new_schedule: The updated schedule
        
    Returns:
        A summary string describing the changes
    """
    changes = []
    
    # Compare triggers section
    old_triggers = old_schedule.get("triggers", [])
    new_triggers = new_schedule.get("triggers", [])
    
    if len(old_triggers) != len(new_triggers):
        changes.append(f"Trigger count changed: {len(old_triggers)} → {len(new_triggers)}")
    
    # Check for changes in time triggers
    old_time_triggers = [t for t in old_triggers if t.get("type") == "time"]
    new_time_triggers = [t for t in new_triggers if t.get("type") == "time"]
    
    if len(old_time_triggers) != len(new_time_triggers):
        changes.append(f"Time trigger count changed: {len(old_time_triggers)} → {len(new_time_triggers)}")
    
    # Check for changes in day_of_week triggers
    old_dow_triggers = [t for t in old_triggers if t.get("type") == "day_of_week"]
    new_dow_triggers = [t for t in new_triggers if t.get("type") == "day_of_week"]
    
    if len(old_dow_triggers) != len(new_dow_triggers):
        changes.append(f"Day of week trigger count changed: {len(old_dow_triggers)} → {len(new_dow_triggers)}")
    
    # Check for changes in event triggers
    old_event_triggers = [t for t in old_triggers if t.get("type") == "event"]
    new_event_triggers = [t for t in new_triggers if t.get("type") == "event"]
    
    if len(old_event_triggers) != len(new_event_triggers):
        changes.append(f"Event trigger count changed: {len(old_event_triggers)} → {len(new_event_triggers)}")
    
    # Compare actions
    old_initial = old_schedule.get("initial_actions", [])
    new_initial = new_schedule.get("initial_actions", [])
    
    if len(old_initial) != len(new_initial):
        changes.append(f"Initial actions count changed: {len(old_initial)} → {len(new_initial)}")
    
    # Compare instructions in triggers
    total_old_instructions = 0
    total_new_instructions = 0
    
    for trigger in old_triggers:
        total_old_instructions += len(trigger.get("actions", []))
    
    for trigger in new_triggers:
        total_new_instructions += len(trigger.get("actions", []))
    
    if total_old_instructions != total_new_instructions:
        changes.append(f"Trigger actions count changed: {total_old_instructions} → {total_new_instructions}")
    
    # Look for changes in variable exports and imports
    old_exports = []
    new_exports = []
    old_imports = []
    new_imports = []
    
    # Check initial actions
    old_initial_exports, old_initial_imports = find_exports_imports(old_initial)
    new_initial_exports, new_initial_imports = find_exports_imports(new_initial)
    
    old_exports.extend(old_initial_exports)
    new_exports.extend(new_initial_exports)
    old_imports.extend(old_initial_imports)
    new_imports.extend(new_initial_imports)
    
    # Check trigger actions
    for trigger in old_triggers:
        e, i = find_exports_imports(trigger.get("actions", []))
        old_exports.extend(e)
        old_imports.extend(i)
    
    for trigger in new_triggers:
        e, i = find_exports_imports(trigger.get("actions", []))
        new_exports.extend(e)
        new_imports.extend(i)
    
    # Report changes in exports/imports
    if set(old_exports) != set(new_exports):
        removed = set(old_exports) - set(new_exports)
        added = set(new_exports) - set(old_exports)
        
        if removed:
            changes.append(f"Removed exports: {', '.join(removed)}")
        if added:
            changes.append(f"Added exports: {', '.join(added)}")
    
    if set(old_imports) != set(new_imports):
        removed = set(old_imports) - set(new_imports)
        added = set(new_imports) - set(old_imports)
        
        if removed:
            changes.append(f"Removed imports: {', '.join(removed)}")
        if added:
            changes.append(f"Added imports: {', '.join(added)}")
    
    # Return the summary
    if changes:
        return "; ".join(changes)
    else:
        return "No significant changes detected" 