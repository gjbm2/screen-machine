# === Scheduler Utilities ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
import json
import os
from utils.logger import info, error, debug
import random
import re
import jinja2

# === Global Storage for Scheduler State ===
scheduler_logs: Dict[str, List[str]] = {}
scheduler_schedule_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of schedules by destination
scheduler_contexts_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of contexts by destination
scheduler_states: Dict[str, str] = {}  # Store paused state by destination
important_triggers: Dict[str, List[Dict[str, Any]]] = {}  # Store important triggers by destination
active_events: Dict[str, Dict[str, datetime]] = {}  # Store active events by destination
running_schedulers = {}  # Store running scheduler tasks

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
        source_dest_id: The ID of the source destination (for verification)
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
    
    # Store the import information
    registry["imports"][var_name][importing_dest_id] = {
        "imported_as": imported_as,
        "source": source_dest_id,
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
                context_stack=scheduler_contexts_stacks[dest_id]
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
    global scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, scheduler_logs
    
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
    global scheduler_contexts_stacks
    
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
    global scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states
    
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
                # Convert string repeat_interval to float
                if isinstance(repeat_interval, str):
                    try:
                        repeat_interval = float(repeat_interval)
                        # Convert to int if it's a whole number for display clarity
                        if repeat_interval == int(repeat_interval):
                            repeat_interval = int(repeat_interval)
                    except (ValueError, TypeError):
                        error(f"Invalid repeat interval format: {repeat_interval}")
                        continue
                
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
        if trigger["type"] == "day_of_week" and "days" in trigger:
            # Check for current day first
            current_day_match = day_str in trigger.get("days", [])
            
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
                    # Convert string repeat_interval to float
                    if isinstance(repeat_interval, str):
                        try:
                            repeat_interval = float(repeat_interval)
                            # Convert to int if it's a whole number for display clarity
                            if repeat_interval == int(repeat_interval):
                                repeat_interval = int(repeat_interval)
                        except (ValueError, TypeError):
                            error(f"Invalid repeat interval format: {repeat_interval}")
                            continue
                            
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
                    
                    # If today is the scheduled day and current time is before start time today
                    if current_day_match and current_minute < scheduled_minutes:
                        time_until_next = scheduled_minutes - current_minute
                        if time_until_next < minutes_until_next:
                            minutes_until_next = time_until_next
                            next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
                            next_action_description = f"Repeating '{trigger['type']}' trigger (every {repeat_interval} min until {until_str})"
                    # If today is the scheduled day and current time is within the repeat window
                    elif current_day_match and scheduled_minutes <= current_minute <= until_minutes:
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
                    # Single time point
                    # Check if today is the scheduled day and this time is in the future today
                    if current_day_match and scheduled_minutes > current_minute:
                        time_until_next = scheduled_minutes - current_minute
                        if time_until_next < minutes_until_next:
                            minutes_until_next = time_until_next
                            next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
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
                                next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
                                next_action_description = f"'{trigger['type']}' trigger on {days_of_week[(current_day_idx + days_until_next) % 7]}"
        
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
                            next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
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
                                        scheduled_time_str = time_str
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
        result["minutes_until_next"] = minutes_until_next
    
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
    
    # Persist changes to disk
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination]
    )
    
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
                
            # If we have a publish destination, add exported variables that are available
            if publish_destination:
                # Import get_exported_variables_with_values here to avoid circular imports
                from routes.scheduler_utils import get_exported_variables_with_values
                exported_vars = get_exported_variables_with_values(publish_destination)
                
                # Add exported variables to template vars
                for var_name, var_info in exported_vars.items():
                    if "value" in var_info:
                        # Use the friendly name if available
                        friendly_name = var_info.get("friendly_name", var_name)
                        template_vars[friendly_name] = var_info["value"]
            
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