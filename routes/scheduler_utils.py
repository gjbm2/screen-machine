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
import threading
from dataclasses import dataclass, field
from collections import deque
import time
import uuid
from routes.utils import get_destinations_for_group

# Thread-safety lock for global state operations
_state_lock = threading.RLock()

def thread_safe(fn):
    """Decorator to ensure thread-safety for functions that modify shared state"""
    def wrapper(*args, **kwargs):
        with _state_lock:
            return fn(*args, **kwargs)
    return wrapper

# === Global Storage for Scheduler State ===
@dataclass
class EventEntry:
    """Represents a scheduled event with metadata."""
    key: str                       # Canonical event identifier (e.g., "user_login")
    active_from: datetime          # When event becomes visible
    expires: datetime              # When event expires
    display_name: str = None       # Optional friendly title for UI/logs
    payload: Any = None            # Optional: Arbitrary data to be carried with the event
    single_consumer: bool = False  # If True, event is removed after first trigger 
    created_at: datetime = field(default_factory=datetime.now)
    unique_id: str = None          # Unique identifier for this specific event instance
    consumed_by: str = None        # Who/what consumed this event (scheduler ID, "expiration", etc.)
    consumed_at: datetime = None   # When the event was consumed
    status: str = "ACTIVE"         # Status: "ACTIVE", "CONSUMED", "EXPIRED"

scheduler_logs: Dict[str, List[str]] = {}
scheduler_schedule_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of schedules by destination
scheduler_contexts_stacks: Dict[str, List[Dict[str, Any]]] = {}  # Store stacks of contexts by destination
scheduler_states: Dict[str, str] = {}  # Store paused state by destination
important_triggers: Dict[str, List[Dict[str, Any]]] = {}  # Store important triggers by destination

# Enhanced event storage:
# destination -> {event_name: deque[EventEntry]}
active_events: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
# Keep history of recent events for UI display (capped length)
event_history: Dict[str, List[Dict[str, Any]]] = {}
# Maximum history entries to retain per destination
from config import MAX_EVENT_HISTORY

# Store running scheduler tasks
running_schedulers = {}

# Add this global variable to store the last execution time of each trigger
# Format: {publish_destination: {trigger_id: timestamp}}
last_trigger_executions = {}

# Add a global variable for rate-limiting debug logging
_last_debug_log_time = {}

# === Additional functions for exported variable registry ===

from config import VARS_REGISTRY_PATH

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
    
    # Avoid duplicating the entry if `output` is exactly the same list object as scheduler_logs[publish_destination]
    if output is not None and output is not scheduler_logs.get(publish_destination, None):
        output.append(formatted_msg)

# === Event Handling Functions ===
def parse_ttl(ttl: Union[str, int, None], default: int = 60) -> int:
    """
    Parse a TTL value in various formats and return seconds.
    
    Args:
        ttl: Time to live in one of these formats:
             - integer: interpreted as seconds
             - string with suffix: e.g. "60s", "5m", "1h", "2d"
             - None: uses the default
        default: Default value in seconds if ttl is None
        
    Returns:
        int: TTL in seconds
    """
    if ttl is None:
        return default
        
    if isinstance(ttl, int) or (isinstance(ttl, str) and ttl.isdigit()):
        return int(ttl)  # Assume seconds
        
    if isinstance(ttl, str):
        # Parse strings like "60s", "5m", "1h", "2d"
        match = re.match(r"^(\d+)([smhd])$", ttl)
        if match:
            value, unit = match.groups()
            value = int(value)
            
            if unit == "s":
                return value
            elif unit == "m":
                return value * 60
            elif unit == "h":
                return value * 3600
            elif unit == "d":
                return value * 86400
                
    # Default
    return default

def parse_duration(duration: Union[str, int, float, None], default_seconds: int = 60) -> int:
    """
    Parse a duration value in various formats and return seconds.
    
    Args:
        duration: Duration in one of these formats:
                - integer or float: interpreted as minutes
                - string with unit suffix: e.g. "60s", "5m", "1h"
                - string without suffix: interpreted as minutes
                - None: uses the default
        default_seconds: Default value in seconds if duration is None
        
    Returns:
        int: Duration in seconds
    """
    # Add rate-limiting for debug logging
    if not hasattr(parse_duration, '_last_debug_log_time'):
        parse_duration._last_debug_log_time = 0
    
    should_log = False
    current_time = time.time()
    if current_time - parse_duration._last_debug_log_time > 30:
        should_log = True
        parse_duration._last_debug_log_time = current_time
    
    # Debug logging
    if should_log:
        debug(f"parse_duration called with: '{duration}' (type: {type(duration).__name__})")
    
    if duration is None:
        return default_seconds
        
    # Handle numeric types (float or int)
    if isinstance(duration, (int, float)):
        return int(duration * 60)  # Convert from minutes to seconds
        
    # Handle string format
    if isinstance(duration, str):
        # Trim whitespace
        duration = duration.strip()
        
        # Check if it has a unit suffix (s, m, h)
        match = re.match(r"^(\d+(?:\.\d+)?)([smhd])$", duration)
        if match:
            value, unit = match.groups()
            value = float(value)
            
            if unit == "s":
                return int(value)
            elif unit == "m":
                return int(value * 60)
            elif unit == "h":
                return int(value * 3600)
            elif unit == "d":
                return int(value * 86400)
        
        # No unit suffix - try to parse as float (minutes)
        try:
            return int(float(duration) * 60)
        except ValueError:
            if should_log:
                debug(f"Could not parse duration '{duration}' as a number")
                
    # Default
    if should_log:
        debug(f"Using default duration of {default_seconds} seconds")
    return default_seconds

@thread_safe
def check_all_expired_events() -> Dict[str, int]:
    """
    Check all events across all destinations for expiration and move expired events to history.
    This function should be called periodically to ensure expired events are properly tracked.
    
    Returns:
        Dict with counts of expired events moved to history by destination
    """
    global active_events, event_history
    now = datetime.now()
    expired_counts = {}
    
    # Process each destination's events
    for dest_id, event_queues in active_events.items():
        total_expired = 0
        keys_to_remove = []
        all_expired_events = []  # Accumulate all expired events for this destination
        # Process each event queue for this destination
        for event_key, queue in event_queues.items():
            expired_events = []
            # Look for expired events
            for event in list(queue):  # Use list to avoid modification during iteration
                if event.expires < now:
                    # Mark as expired
                    event.status = "EXPIRED"
                    event.consumed_by = "expiration"
                    event.consumed_at = now
                    
                    # Add to expired list
                    expired_events.append(event)
                    
                    # Remove from active queue
                    try:
                        queue.remove(event)
                    except ValueError:
                        pass  # Event might have been removed already
                    
                    # Log expiration
                    info(f"EVENT EXPIRED: '{event.key}' [id:{event.unique_id}] that was created at {event.created_at}")
            # If the queue is now empty, mark the key for removal
            if len(queue) == 0:
                keys_to_remove.append(event_key)
            all_expired_events.extend(expired_events)  # Accumulate for the destination
        # Remove empty event keys after processing
        for key in keys_to_remove:
            del event_queues[key]
        # Add expired events to history
        if all_expired_events:
            if dest_id not in event_history:
                event_history[dest_id] = []
            event_history[dest_id].extend(all_expired_events)
            total_expired += len(all_expired_events)
            # Keep history size under control
            if len(event_history[dest_id]) > MAX_EVENT_HISTORY:
                event_history[dest_id] = event_history[dest_id][-MAX_EVENT_HISTORY:]
        # Record count for this destination
        if total_expired > 0:
            expired_counts[dest_id] = total_expired
            info(f"Moved {total_expired} expired events to history for '{dest_id}'")
            # Save state to persist changes
            update_scheduler_state(dest_id, force_save=True)
    
    return expired_counts

def parse_time(time_val: Union[str, datetime, None]) -> Optional[datetime]:
    """
    Parse a time/date value in various formats.
    
    Args:
        time_val: A time value in one of these formats:
                 - ISO-8601 datetime string: "2023-04-01T14:30:00.000Z"
                 - Future time offset: "+5m", "+1h", "+2d"
                 - datetime object: returned as-is
                 - None: returns None
    
    Returns:
        datetime or None: Parsed datetime object, or None if input is None
    """
    if time_val is None:
        return None
        
    if isinstance(time_val, datetime):
        return time_val
        
    if isinstance(time_val, str):
        try:
            return datetime.fromisoformat(time_val)
        except ValueError:
            error(f"Invalid time format: {time_val}")
            return None
    
    return None



@thread_safe
def throw_event(
    scope: str,
    key: str,
    ttl: Union[str, int] = "60s",
    delay: Union[str, int, None] = None,
    future_time: Union[str, datetime, None] = None,
    display_name: Optional[str] = None,
    payload: Any = None,
    single_consumer: bool = False
) -> Dict[str, Any]:
    """
    Create and store an event for all destinations resolved by the scope.
    """
    global active_events, event_history

    now = datetime.now()

    # Calculate activation time
    if future_time is not None:
        active_from = parse_time(future_time)
        if active_from is None:
            active_from = now  # Fallback to now if parsing failed
    elif delay is not None:
        delay_seconds = parse_ttl(delay, 0)
        active_from = now + timedelta(seconds=delay_seconds)
    else:
        active_from = now

    # Calculate expiry
    ttl_seconds = parse_ttl(ttl)
    expires = active_from + timedelta(seconds=ttl_seconds)

    # Get all destinations for this scope
    pub_dests = get_destinations_for_group(scope)

    result = {
        "status": "queued",
        "key": key,
        "active_from": active_from.isoformat(),
        "expires": expires.isoformat(),
        "unique_ids": [],
        "destinations": pub_dests,
    }

    activation_info = ""
    if active_from > now:
        activation_info = f", activates in {(active_from - now).total_seconds():.1f}s"
    info(f"EVENT CREATED: '{key}' for scope '{scope}', expires in {ttl_seconds}s{activation_info}")

    for dest in pub_dests:
        if dest not in active_events:
            active_events[dest] = {}
        if key not in active_events[dest]:
            active_events[dest][key] = deque()
        dest_event_uuid = str(uuid.uuid4())
        dest_entry = EventEntry(
            key=key,
            active_from=active_from,
            expires=expires,
            display_name=display_name,
            payload=payload,
            single_consumer=single_consumer,
            created_at=now,
            unique_id=dest_event_uuid,
            status="ACTIVE"
        )
        active_events[dest][key].append(dest_entry)
        result["unique_ids"].append(dest_event_uuid)
        if dest not in event_history:
            event_history[dest] = []
        event_history[dest].append(dest_entry)

    return result

@thread_safe
def pop_next_event(dest_id: str, event_key: str, now: datetime = None, consumer_id: str = None, event_trigger_mode: bool = False) -> Optional[EventEntry]:
    """
    Get and optionally remove the next available event matching the key.
    
    Args:
        dest_id: Destination ID
        event_key: Event identifier
        now: Current time (defaults to utcnow if None)
        consumer_id: Optional ID of the consumer (defaults to destination ID)
        event_trigger_mode: If True, prioritize consumption over expiration
        
    Returns:
        EventEntry if found and active, None otherwise
    """
    global active_events, event_history
    
    if now is None:
        now = datetime.now()
        
    # Default consumer_id to destination ID if not provided
    if consumer_id is None:
        consumer_id = f"scheduler_{dest_id}"
        
    # Check destination-specific queue only (no more group events)
    if dest_id in active_events and event_key in active_events[dest_id]:
        queue = active_events[dest_id][event_key]
        if not queue:  # Handle empty queue case
            return None
        
        # Log that we're checking for this event
        info(f"EVENT CHECK: Looking for '{event_key}' in destination '{dest_id}' queue ({len(queue)} events)")
        
        # Get event and handle expired events - pass the event_trigger_mode flag
        valid_event, expired_events = _pop_event_from_queue(queue, now, event_trigger_mode)
        
        # Add any expired events to history
        if expired_events:
            if dest_id not in event_history:
                event_history[dest_id] = []
            
            event_history[dest_id].extend(expired_events)
            
            # Keep history size under control
            if len(event_history[dest_id]) > MAX_EVENT_HISTORY:
                # Remove oldest events when we exceed the limit
                event_history[dest_id] = event_history[dest_id][-MAX_EVENT_HISTORY:]
                
            info(f"Added {len(expired_events)} expired events to history for '{dest_id}'")
        
        # Add consumed event to history if we found one
        if valid_event:
            # Mark the event as consumed
            valid_event.status = "CONSUMED"
            valid_event.consumed_by = consumer_id
            valid_event.consumed_at = now
            # Add consumed event to history
            if dest_id not in event_history:
                event_history[dest_id] = []
            event_history[dest_id].append(valid_event)
            
            # Keep history size under control
            if len(event_history[dest_id]) > MAX_EVENT_HISTORY:
                # Remove oldest events when we exceed the limit
                event_history[dest_id] = event_history[dest_id][-MAX_EVENT_HISTORY:]
                
            info(f"EVENT CONSUMED: '{event_key}' [id:{valid_event.unique_id}] from destination '{dest_id}' by {consumer_id}, {len(queue)} events remaining in queue")
        
        return valid_event
            
    return None

def _pop_event_from_queue(queue: deque, now: datetime, event_trigger_mode: bool = False) -> tuple:
    """
    Helper to get the next event from a queue, handling expiry and activation time.
    
    Args:
        queue: Queue of EventEntry objects
        now: Current time
        event_trigger_mode: If True, prioritize consumption over expiration checking
        
    Returns:
        Tuple of (valid_event, expired_events) where:
        - valid_event: EventEntry if found and active, None otherwise
        - expired_events: List of expired events that were removed from the queue
    """
    # Handle empty queue case
    if not queue:
        debug("Queue is empty, returning None")
        return None, []
    
    # Make a copy of the queue to avoid modifying while iterating
    items = list(queue)
    
    # Track valid event and expired events
    valid_event = None
    expired_events = []
    
    # In event trigger mode, first look for consumable events before expiring anything
    if event_trigger_mode:
        # First pass: look for valid events without expiring any
        for event in items:
            try:
                # Check if event is active (not expired and not in the future)
                if event.expires > now and event.active_from <= now:
                    valid_event = event
                    break  # We found a valid event to consume, stop looking
            except Exception as e:
                error(f"Error processing event: {str(e)}")
                debug(f"Problem event: {event}")
                
        # If we found a valid event, remove it from the queue and return it
        if valid_event:
            try:
                queue.remove(valid_event)
                # If this is a single-consumer event, mark it as consumed
                if valid_event.single_consumer:
                    valid_event.status = "CONSUMED"
                    valid_event.consumed_at = now
                    # The consumer will be set in pop_next_event
                else:
                    # For non-single-consumer events, we still remove it from queue
                    # but we don't mark it as consumed since it's reusable
                    pass
            except ValueError:
                pass  # Event might have been removed already
                
            # In event trigger mode, we can leave expired events for the next check
            # This ensures we prioritize consumption over expiration
            return valid_event, []
    
    # Regular flow (non-event trigger mode) or no valid event was found in trigger mode
    # Check all events for expiration first
    for i, event in enumerate(items):
        try:
            # Convert times to the same timezone for comparison
            event_expires = event.expires
            event_active_from = event.active_from
            
            # Handle expiration - ONLY IF the event has become active
            # An event should not expire if it hasn't even had a chance to be active yet
            if event_expires < now and event_active_from <= now:
                # Mark as expired before adding to expired_events
                event.status = "EXPIRED"
                event.consumed_by = "expiration"
                event.consumed_at = now
                
                # Log event expiration
                info(f"EVENT EXPIRED: '{event.key}' [id:{event.unique_id}] that was created at {event.created_at}")
                expired_events.append(event)
                # We'll remove it from the queue later
                continue
                
            # Check if event is active yet
            if event_active_from > now:
                debug(f"Event {event.key} [id:{event.unique_id}] is not active yet")
                continue
                
            # First active event found - we'll return this one
            if valid_event is None:
                debug(f"Found valid event {event.key} [id:{event.unique_id}]")
                valid_event = event
                # We'll remove it from the queue later if single_consumer
                # (We don't break here because we still need to check other events for expiration)
                
        except Exception as e:
            error(f"Error processing event: {str(e)}")
            debug(f"Problem event: {event}")
    
    # Now remove expired events from the queue
    for event in expired_events:
        try:
            queue.remove(event)
        except ValueError:
            pass  # Event might have been removed already
    # If the queue is now empty after removing expired events, try to remove the key from active_events
    # (We need to find the parent dict, so this is best-effort)
    try:
        if hasattr(queue, '__parent_dict__'):
            parent_dict = queue.__parent_dict__
            for k, v in list(parent_dict.items()):
                if v is queue and len(queue) == 0:
                    del parent_dict[k]
    except Exception:
        pass
    
    # Update the valid event if it's being consumed
    if valid_event:
        try:
            queue.remove(valid_event)
            # If this is a single-consumer event, mark it as consumed
            if valid_event.single_consumer:
                valid_event.status = "CONSUMED"
                valid_event.consumed_at = now
                # The consumer will be set in pop_next_event
            else:
                # For non-single-consumer events, we still remove it from queue
                # but we don't mark it as consumed since it's reusable
                pass
        except ValueError:
            pass  # Event might have been removed already
    
    return valid_event, expired_events

def get_events_for_destination(dest_id: str, include_group_events: bool = True) -> Dict[str, Any]:
    """
    Get all events available to a destination.
    
    Args:
        dest_id: Destination ID
        include_group_events: Whether to include events from groups this destination belongs to
        
    Returns:
        Dict with queue and history
    """
    global active_events, event_history
    
    now = datetime.now()
    result = {
        "queue": [],
        "history": []
    }
    
    # Get destination-specific events
    dest_events = []
    if dest_id in active_events:
        for key, event_queue in active_events[dest_id].items():
            # Filter out expired events but keep the queue structure
            active_entries = [entry for entry in event_queue if entry.expires > now]
            if active_entries:
                for entry in active_entries:
                    dest_events.append({
                        "key": entry.key,
                        "display_name": entry.display_name,
                        "scope": dest_id,  # Use actual destination ID as scope
                        "active_from": entry.active_from.isoformat(),
                        "expires": entry.expires.isoformat(),
                        "has_payload": entry.payload is not None,
                        "single_consumer": entry.single_consumer,
                        "created_at": entry.created_at.isoformat(),
                        "unique_id": entry.unique_id,
                        "status": entry.status
                    })
    
    # Add destination events to queue
    result["queue"].extend(dest_events)
    
    # Add history - only include events NOT in the active queue
    if dest_id in event_history:
        active_event_keys = set()
        if dest_id in active_events:
            for key, event_queue in active_events[dest_id].items():
                for entry in event_queue:
                    if entry.expires > now:  # Only consider non-expired events
                        active_event_keys.add(entry.key)
        
        # Only include events in history that aren't in the active queue
        for entry in event_history[dest_id]:
            # Skip events that are still active in the queue
            if entry.key in active_event_keys and entry.expires > now:
                continue
                
            history_entry = {
                "key": entry.key,
                "display_name": entry.display_name,
                "scope": dest_id,  # Use actual destination ID as scope
                "active_from": entry.active_from.isoformat(),
                "expires": entry.expires.isoformat(),
                "has_payload": entry.payload is not None,
                "single_consumer": entry.single_consumer,
                "created_at": entry.created_at.isoformat(),
                "unique_id": entry.unique_id,
                "status": entry.status
            }
            
            # Add consumption info if available
            if entry.consumed_at:
                history_entry["consumed_at"] = entry.consumed_at.isoformat()
            if entry.consumed_by:
                history_entry["consumed_by"] = entry.consumed_by
                
            result["history"].append(history_entry)
    
    # Log summary of events found
    total_events = len(result["queue"]) + len(result["history"])
    if total_events > 0:
        info(f"EVENT STATE: Found {len(result['queue'])} active events and {len(result['history'])} history events for '{dest_id}'")
    else:
        info(f"EVENT STATE: No events found for '{dest_id}'")
    
    return result

def clear_events_for_destination(dest_id: str, event_key: Optional[str] = None, event_id: Optional[str] = None, clear_history: bool = False) -> Dict[str, int]:
    """
    Clear events for a destination.
    
    Args:
        dest_id: Destination ID
        event_key: Optional specific event key to clear
        event_id: Optional specific event ID (unique_id) to clear
        clear_history: Whether to also clear event history
        
    Returns:
        Dict with counts of cleared events
    """
    global active_events, event_history
    
    cleared_active = 0
    cleared_history = 0
    
    # Clear active events
    if dest_id in active_events:
        if event_key and not event_id:
            # Clear by event key
            if event_key in active_events[dest_id]:
                cleared_active = len(active_events[dest_id][event_key])
                active_events[dest_id][event_key].clear()
        elif event_id:
            # Clear by event ID
            for key, event_queue in active_events[dest_id].items():
                for i, event in enumerate(list(event_queue)):
                    if event.unique_id == event_id:
                        event_queue.remove(event)
                        cleared_active += 1
                        info(f"Cleared active event ID {event_id} (key: {event.key}) from {dest_id}")
        else:
            # Clear all events
            for key in list(active_events[dest_id].keys()):
                cleared_active += len(active_events[dest_id][key])
                active_events[dest_id][key].clear()
    
    # Clear history if requested
    if clear_history and dest_id in event_history:
        if event_key and not event_id:
            # Remove events with matching key
            original_len = len(event_history[dest_id])
            event_history[dest_id] = [e for e in event_history[dest_id] if e.key != event_key]
            cleared_history = original_len - len(event_history[dest_id])
        elif event_id:
            # Remove event with matching ID
            original_len = len(event_history[dest_id])
            event_history[dest_id] = [e for e in event_history[dest_id] if e.unique_id != event_id]
            cleared_history = original_len - len(event_history[dest_id])
            if cleared_history > 0:
                info(f"Cleared history event ID {event_id} from {dest_id}")
        else:
            # Clear all history
            cleared_history = len(event_history[dest_id])
            event_history[dest_id] = []
    
    return {
        "cleared_active": cleared_active,
        "cleared_history": cleared_history,
        "total_cleared": cleared_active + cleared_history
    }

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

@thread_safe
def load_scheduler_state(publish_destination: str) -> Dict[str, Any]:
    """Load scheduler state from disk."""
    global scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, active_events, event_history, last_trigger_executions, scheduler_logs
    
    # Initialize global variables if they don't exist
    if publish_destination not in scheduler_schedule_stacks:
        scheduler_schedule_stacks[publish_destination] = []
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = []
    if publish_destination not in scheduler_states:
        scheduler_states[publish_destination] = "stopped"
    if publish_destination not in scheduler_logs:
        scheduler_logs[publish_destination] = []
    if publish_destination not in last_trigger_executions:
        last_trigger_executions[publish_destination] = {}
    if publish_destination not in active_events:
        active_events[publish_destination] = {}
    if publish_destination not in event_history:
        event_history[publish_destination] = []
    
    path = get_scheduler_storage_path(publish_destination)
    data = {}
    
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                data = json.load(f)
                
                # Load schedule stack
                if "schedule_stack" in data:
                    scheduler_schedule_stacks[publish_destination] = data["schedule_stack"]
                    
                # Load context stack
                if "context_stack" in data:
                    scheduler_contexts_stacks[publish_destination] = data["context_stack"]
                    
                # Load scheduler state
                if "state" in data:
                    scheduler_states[publish_destination] = data["state"]
                
                # Load active events
                if "active_events" in data:
                    active_events_data = data["active_events"]
                    if publish_destination not in active_events:
                        active_events[publish_destination] = {}
                        
                    for key, events_list in active_events_data.items():
                        active_events[publish_destination][key] = deque()
                        for event_data in events_list:
                            try:
                                # Create an EventEntry object from the saved data
                                active_from = _parse_iso_datetime(event_data["active_from"])
                                expires = _parse_iso_datetime(event_data["expires"])
                                created_at = _parse_iso_datetime(event_data["created_at"])
                                
                                # Handle possible additional consumption fields
                                consumed_at = None
                                if "consumed_at" in event_data and event_data["consumed_at"]:
                                    consumed_at = _parse_iso_datetime(event_data["consumed_at"])
                                
                                # Create the event entry
                                entry = EventEntry(
                                    key=event_data["key"],
                                    active_from=active_from,
                                    expires=expires,
                                    display_name=event_data.get("display_name"),
                                    payload=event_data.get("payload"),
                                    single_consumer=event_data.get("single_consumer", False),
                                    created_at=created_at,
                                    unique_id=event_data.get("unique_id"),
                                    status=event_data.get("status", "ACTIVE"),
                                    consumed_by=event_data.get("consumed_by"),
                                    consumed_at=consumed_at
                                )
                                active_events[publish_destination][key].append(entry)
                            except Exception as e:
                                error(f"Error restoring event {event_data.get('key', 'unknown')}: {str(e)}")
                
                # Load event history
                if "event_history" in data:
                    history_data = data["event_history"]
                    if publish_destination not in event_history:
                        event_history[publish_destination] = []
                        
                    for event_data in history_data:
                        try:
                            # Create an EventEntry object from the saved data
                            active_from = _parse_iso_datetime(event_data["active_from"])
                            expires = _parse_iso_datetime(event_data["expires"])
                            created_at = _parse_iso_datetime(event_data["created_at"])
                            
                            # Handle possible additional consumption fields
                            consumed_at = None
                            if "consumed_at" in event_data and event_data["consumed_at"]:
                                consumed_at = _parse_iso_datetime(event_data["consumed_at"])
                            
                            # Create the event entry
                            entry = EventEntry(
                                key=event_data["key"],
                                active_from=active_from,
                                expires=expires,
                                display_name=event_data.get("display_name"),
                                payload=event_data.get("payload"),
                                single_consumer=event_data.get("single_consumer", False),
                                created_at=created_at,
                                unique_id=event_data.get("unique_id"),
                                status=event_data.get("status", "ACTIVE"),
                                consumed_by=event_data.get("consumed_by"),
                                consumed_at=consumed_at
                            )
                            event_history[publish_destination].append(entry)
                        except Exception as e:
                            error(f"Error restoring history event {event_data.get('key', 'unknown')}: {str(e)}")
                
                # Load last trigger executions
                if "last_trigger_executions" in data:
                    trigger_executions = {}
                    for trigger_id, execution_time_str in data["last_trigger_executions"].items():
                        try:
                            if execution_time_str and isinstance(execution_time_str, str):
                                trigger_executions[trigger_id] = _parse_iso_datetime(execution_time_str)
                            else:
                                trigger_executions[trigger_id] = None
                        except Exception as e:
                            error(f"Error parsing trigger execution time: {str(e)}")
                    last_trigger_executions[publish_destination] = trigger_executions
                    
                info(f"Loaded scheduler state for {publish_destination}, state: {scheduler_states.get(publish_destination, 'unknown')}")
        else:
            info(f"No existing state file found for {publish_destination}, starting with empty state")
            # Create new state with empty values
            new_state = {
                "schedule_stack": [],
                "context_stack": [],
                "state": "stopped",
                "last_updated": datetime.now().isoformat()
            }
            try:
                save_scheduler_state(publish_destination, new_state)
            except Exception as e:
                error(f"Error saving initial scheduler state: {str(e)}")
            
    except Exception as e:
        error(f"Error loading scheduler state for {publish_destination}: {str(e)}")
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        
    return data

def _parse_iso_datetime(dt_str):
    """Helper function to parse ISO format datetime strings."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except Exception:
        # Fallback for different ISO formats
        try:
            from dateutil import parser
            return parser.parse(dt_str)
        except Exception as e:
            error(f"Failed to parse datetime: {dt_str} - {str(e)}")
            return datetime.now()  # Default to current time
    
@thread_safe
def save_scheduler_state(publish_destination: str, state: Dict[str, Any] = None) -> None:
    """Save the scheduler state to disk for a destination."""
    path = get_scheduler_storage_path(publish_destination)
    
    # Add rate-limiting for debug logging
    if not hasattr(save_scheduler_state, '_last_debug_log_time'):
        save_scheduler_state._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    if publish_destination not in save_scheduler_state._last_debug_log_time or \
       (current_time - save_scheduler_state._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        save_scheduler_state._last_debug_log_time[publish_destination] = current_time
    
    if should_log:
        debug(f"Starting state save for {publish_destination} to {path}")
    
    try:
        # Check what state was passed in
        if should_log:
            if state is not None:
                passed_in_state = state.get("state", "none-passed")
                debug(f"State object passed with state='{passed_in_state}' for {publish_destination}")
            else:
                debug(f"No state object passed for {publish_destination}")
            
            # Check what's in memory
            in_memory_state = scheduler_states.get(publish_destination, "not-in-memory")
            debug(f"Current in-memory state is '{in_memory_state}' for {publish_destination}")
    
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
        
        # Save destination-specific events
        if publish_destination in active_events:
            events_data = {}
            for key, event_queue in active_events[publish_destination].items():
                events_data[key] = [
                    {
                        "key": event.key,
                        "active_from": event.active_from.isoformat(),
                        "expires": event.expires.isoformat(),
                        "display_name": event.display_name,
                        "payload": event.payload,
                        "single_consumer": event.single_consumer,
                        "created_at": event.created_at.isoformat(),
                        "unique_id": event.unique_id,
                        "status": event.status,
                        "consumed_by": event.consumed_by,
                        "consumed_at": event.consumed_at.isoformat() if event.consumed_at else None
                    }
                    for event in event_queue
                ]
            state_to_save["active_events"] = events_data
        
        # Save event history for this destination
        if publish_destination in event_history:
            history_data = [
                {
                    "key": event.key,
                    "active_from": event.active_from.isoformat(),
                    "expires": event.expires.isoformat(),
                    "display_name": event.display_name,
                    "payload": event.payload,
                    "single_consumer": event.single_consumer,
                    "created_at": event.created_at.isoformat(),
                    "unique_id": event.unique_id,
                    "status": event.status,
                    "consumed_by": event.consumed_by,
                    "consumed_at": event.consumed_at.isoformat() if event.consumed_at else None
                }
                for event in event_history[publish_destination]
            ]
            state_to_save["event_history"] = history_data
        
        if should_log:
            debug(f"Will save state='{state_to_save['state']}' for {publish_destination}")
        
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
        if should_log and state_to_save["context_stack"]:
            context_count = len(state_to_save["context_stack"])
            debug(f"[SAVING STATE] {publish_destination} has {context_count} contexts")
            
            if context_count > 0:
                first_context = state_to_save["context_stack"][0]
                if "vars" in first_context:
                    var_count = len(first_context["vars"])
                    var_names = ", ".join(list(first_context["vars"].keys()))
                    debug(f"[SAVING STATE] First context has {var_count} vars: {var_names}")
        
        # Log the current state we're saving
        if should_log:
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
        if should_log:
            debug(f"Successfully saved state='{state_to_save['state']}' for {publish_destination}")
        
    except Exception as e:
        if should_log:
            debug(f"Failed to save state for {publish_destination}: {str(e)}")
        error(f"CRITICAL ERROR: Failed to save state for {publish_destination}: {str(e)}")
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")

@thread_safe
def update_scheduler_state(publish_destination: str, 
                         schedule_stack: Optional[List[Dict[str, Any]]] = None,
                         context_stack: Optional[List[Dict[str, Any]]] = None,
                         state: Optional[str] = None,
                         force_save: bool = False) -> None:
    """Update parts of the scheduler state and then save everything to disk."""
    # Add rate-limiting for debug logging
    if not hasattr(update_scheduler_state, '_last_debug_log_time'):
        update_scheduler_state._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    if publish_destination not in update_scheduler_state._last_debug_log_time or \
       (current_time - update_scheduler_state._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        update_scheduler_state._last_debug_log_time[publish_destination] = current_time
    
    # Simple, unconditional updating
    if should_log:
        debug(f"Starting state update for {publish_destination}")
    
    # Check current state before update
    before_state = scheduler_states.get(publish_destination, "not-in-memory")
    if should_log:
        debug(f"Before update, in-memory state is '{before_state}' for {publish_destination}")
    
    # Update in-memory state with any provided values
    if schedule_stack is not None:
        scheduler_schedule_stacks[publish_destination] = schedule_stack
        if should_log:
            debug(f"Updated schedule_stack: {len(schedule_stack)} items")
    
    if context_stack is not None:
        scheduler_contexts_stacks[publish_destination] = context_stack
        if should_log:
            debug(f"Updated context_stack: {len(context_stack)} contexts")
            if context_stack and "vars" in context_stack[-1]:
                debug(f"Top context vars: {list(context_stack[-1].get('vars', {}).keys())}")
    
    if state is not None:
        if should_log:
            debug(f"Updating state from '{before_state}' to '{state}' for {publish_destination}")
        scheduler_states[publish_destination] = state
    else:
        if should_log:
            debug(f"No state provided, keeping current state '{before_state}' for {publish_destination}")
    
    # Check state after update
    after_state = scheduler_states.get(publish_destination, "not-in-memory")
    if should_log:
        debug(f"After update, in-memory state is '{after_state}' for {publish_destination}")
    
    # Always save the full state to disk
    if should_log:
        debug(f"Calling save_scheduler_state for {publish_destination}")
    
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
        if should_log:
            debug(f"Force-saved complete state for {publish_destination}")
    else:
        # Use default save behavior
        save_scheduler_state(publish_destination)
    
    if should_log:
        debug(f"Completed state update for {publish_destination}")

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
        
    # Add throttling for debug logging
    if not hasattr(extract_instructions, '_last_debug_log_time'):
        extract_instructions._last_debug_log_time = {}
    
    should_log_debug = False
    current_time = time.time()
    log_key = id(instruction_container)  # Use container object ID as key
    
    # Only log once every 30 seconds for the same container
    if log_key not in extract_instructions._last_debug_log_time or \
       (current_time - extract_instructions._last_debug_log_time.get(log_key, 0)) > 30:
        should_log_debug = True
        extract_instructions._last_debug_log_time[log_key] = current_time
    
    # Only log if throttling allows it
    if should_log_debug:
        debug(f"Extracting instructions from container type: {type(instruction_container).__name__}")
    
    # Check if this is an instruction array object with an instructions_block
    if isinstance(instruction_container, dict) and "instructions_block" in instruction_container:
        result = instruction_container.get("instructions_block", [])
        if should_log_debug and len(result) > 0:
            debug(f"Found instructions_block with {len(result)} instructions")
        return result
        
    # Fallback for old format or direct instruction arrays
    if isinstance(instruction_container, dict) and "instructions" in instruction_container:
        result = instruction_container.get("instructions", [])
        if should_log_debug and len(result) > 0:
            debug(f"Found instructions with {len(result)} instructions")
        return result
    
    # Check for direct trigger_actions format
    if isinstance(instruction_container, dict) and "trigger_actions" in instruction_container:
        # This could be a direct trigger object, look in trigger_actions
        trigger_actions = instruction_container.get("trigger_actions", {})
        if isinstance(trigger_actions, dict) and "instructions_block" in trigger_actions:
            result = trigger_actions.get("instructions_block", [])
            if should_log_debug and len(result) > 0:
                debug(f"Found instructions_block in trigger_actions with {len(result)} instructions")
            return result
        if isinstance(trigger_actions, dict) and "instructions" in trigger_actions:
            result = trigger_actions.get("instructions", [])
            if should_log_debug and len(result) > 0:
                debug(f"Found instructions in trigger_actions with {len(result)} instructions")
            return result
    
    # Check if this might be a direct array of instructions
    if isinstance(instruction_container, list):
        if should_log_debug and len(instruction_container) > 0:
            debug(f"Container is a list with {len(instruction_container)} instructions")
        return instruction_container
    
    # If it's a single instruction wrapped in a dict
    if isinstance(instruction_container, dict) and "action" in instruction_container:
        if should_log_debug:
            debug("Container appears to be a single instruction")
        return [instruction_container]
    
    if should_log_debug:
        debug("No instructions found in container")
    return []

def process_time_schedules(time_schedules: List[Dict[str, Any]], now: datetime, minute_of_day: int, publish_destination: str = None, apply_grace_period: bool = False) -> List[Dict[str, Any]]:
    """Process a list of time schedules and return matched schedules without extracting instructions yet.
    
    Args:
        time_schedules: List of time schedule objects to process
        now: Current datetime
        minute_of_day: Current minute of day
        publish_destination: Optional destination ID for logging
        apply_grace_period: Whether to apply grace period for missed events (True for init, False for load)
    
    Returns:
        List of matched schedule objects
    """
    if not time_schedules:
        return []
        
    current_time_str = now.strftime("%H:%M")
    matched_schedules = []
    
    # Initialize last execution tracking for this destination if it doesn't exist
    global last_trigger_executions
    if publish_destination not in last_trigger_executions:
        last_trigger_executions[publish_destination] = {}
    
    # Add rate-limiting for debug logging
    if not hasattr(process_time_schedules, '_last_debug_log_time'):
        process_time_schedules._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    
    # Only log once every 30 seconds per destination
    if publish_destination not in process_time_schedules._last_debug_log_time or \
       (current_time - process_time_schedules._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        process_time_schedules._last_debug_log_time[publish_destination] = current_time
    
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
                    
                    # Create a unique identifier for this specific interval that is stable for the *current day*.
                    # We include the ISO-formatted date to avoid clashes between identical interval numbers on
                    # different days (e.g. yesterday's `_14` vs today's `_14`).
                    # Format: <schedule_hash>_<YYYY-MM-DD>_<interval_number>
                    interval_id = f"{schedule_id}_{now.date().isoformat()}_{current_interval}"
                    
                    # Only log debug info if we're not throttling
                    if should_log:
                        debug(f"Schedule check: ID={interval_id}, current={now}, " +
                              f"expected_execution={expected_execution_time}, next={next_expected_time}, " +
                              f"interval={repeat_interval}m, seconds_since_start={seconds_since_start}s, " +
                              f"interval_seconds={interval_seconds}s, current_interval={current_interval}")
                    
                    # Only execute if:
                    # 1. This specific interval hasn't been executed yet
                    # 2. We're within 10 seconds of the expected execution time or
                    #    this is the most recent interval and we missed it by less than the grace period
                    time_since_expected = (now - expected_execution_time).total_seconds()
                    is_close_to_expected = abs(time_since_expected) < 10  # Within 10 seconds of expected time
                    # Only allow catch-up execution if grace period is enabled and we are within the grace window
                    is_latest_missed = apply_grace_period and (0 < time_since_expected <= LATE_EXECUTION_GRACE_PERIOD_SECONDS)
                    
                    if (interval_id not in last_trigger_executions[publish_destination] and
                        (is_close_to_expected or is_latest_missed)):
                        
                        # Record this execution
                        last_trigger_executions[publish_destination][interval_id] = now
                        
                        message = f"Matched repeating time schedule at {current_time_str} (every {repeat_interval} minutes until {until_str})"
                        info(message)
                        if publish_destination:
                            log_schedule(message, publish_destination, now)
                        
                        # Only log if we're not throttling - and only if we're actually executing
                        if should_log:
                            debug(f"Executing schedule (ID={interval_id}): current={now}, " +
                                  f"interval={repeat_interval}m, seconds_since_start={seconds_since_start}s, " +
                                  f"expected_time={expected_execution_time}, time_since_expected={time_since_expected}s")
                        
                        matched_schedules.append(schedule)
                    elif should_log:
                        # Only log skipped executions if we're not throttling
                        if interval_id in last_trigger_executions[publish_destination]:
                            debug(f"Skipping execution (ID={interval_id}): already executed this interval")
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
                elif should_log:
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
        
        # Add rate-limiting for debug logging
        if not hasattr(get_next_scheduled_action, '_last_debug_log_time'):
            get_next_scheduled_action._last_debug_log_time = {}
        
        should_log = False
        current_time = now.timestamp()
        if publish_destination not in get_next_scheduled_action._last_debug_log_time or \
           (current_time - get_next_scheduled_action._last_debug_log_time.get(publish_destination, 0)) > 30:
            should_log = True
            get_next_scheduled_action._last_debug_log_time[publish_destination] = current_time
        
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
                        
                        if should_log:
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
                                if should_log:
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
                            
                            if should_log:
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
                                if should_log:
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
            "description": None,
            "minutes_until_next": 999999999,
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
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
            
            # Add now() function to Jinja environment
            env.globals['now'] = datetime.now
            # Add timedelta for date/time calculations
            env.globals['timedelta'] = timedelta
            
            template = env.from_string(value)
            
            # Create template variables from context
            template_vars = {}
            
            # Add all variables from context
            if "vars" in context:
                template_vars.update(context["vars"])
            
            # Add _current_destination as a special variable (always available when publish_destination is provided)
            if publish_destination:
                template_vars["_current_destination"] = publish_destination
            
            # Add _event if it exists in the context (crucial for event-triggered actions)
            if "_event" in context:
                template_vars["_event"] = context["_event"]
                # Also make it available as 'event' (without underscore) for convenience
                template_vars["event"] = context["_event"]
            
            # Also check if there's a current event being processed
            if "current_event" in context:
                # If _event isn't already set, use current_event as _event
                if "_event" not in template_vars:
                    template_vars["_event"] = context["current_event"]
                    template_vars["event"] = context["current_event"]
                # Also make it available as current_event
                template_vars["current_event"] = context["current_event"]
            
            # Add _current_image special variable - dynamically resolved
            if publish_destination:
                try:
                    from routes.publisher import get_published_info
                    from routes.bucketer import bucket_path
                    from pathlib import Path
                    import os
                    import shutil
                    import uuid
                    
                    # Get the published info to determine the actual image location
                    published_info = get_published_info(publish_destination)
                    if published_info and published_info.get("published"):
                        published_filename = published_info["published"]
                        
                        # CRITICAL: Always prefer the bucket path over raw_url to get the immutable file
                        # The raw_url might point to output/destination.jpg which gets overwritten
                        bucket_dir = bucket_path(publish_destination)
                        bucket_image_path = bucket_dir / published_filename
                        
                        if bucket_image_path.exists():
                            # Use the bucket path - this is the immutable file
                            template_vars["_current_image"] = str(bucket_image_path)
                            template_vars["current_image"] = str(bucket_image_path)
                            debug(f"_current_image resolved to bucket path: {bucket_image_path}")
                        else:
                            # Bucket file doesn't exist - need to create an immutable copy
                            raw_url = published_info.get("raw_url")
                            if raw_url:
                                current_image_path = raw_url.lstrip("/")
                                
                                # Check if the current image file exists
                                if os.path.exists(current_image_path):
                                    try:
                                        # Create an immutable copy in a temp location within the bucket directory
                                        bucket_dir.mkdir(parents=True, exist_ok=True)
                                        
                                        # Generate a unique filename for the immutable copy
                                        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                                        unique_id = str(uuid.uuid4())[:8]
                                        source_path = Path(current_image_path)
                                        immutable_filename = f"{timestamp}-{unique_id}{source_path.suffix}"
                                        immutable_path = bucket_dir / immutable_filename
                                        
                                        # Copy the current image to create an immutable version
                                        shutil.copy2(current_image_path, immutable_path)
                                        
                                        # Use the immutable copy
                                        template_vars["_current_image"] = str(immutable_path)
                                        template_vars["current_image"] = str(immutable_path)
                                        
                                        # Log this action for debugging
                                        debug(f"Created immutable copy for _current_image: {current_image_path} -> {immutable_path}")
                                    except Exception as copy_error:
                                        # If copying fails, fall back to the original path
                                        error(f"Failed to create immutable copy of {current_image_path}: {copy_error}")
                                        template_vars["_current_image"] = current_image_path
                                        template_vars["current_image"] = current_image_path
                                else:
                                    # Current image file doesn't exist, fallback to raw path
                                    template_vars["_current_image"] = current_image_path
                                    template_vars["current_image"] = current_image_path
                            else:
                                template_vars["_current_image"] = ""
                                template_vars["current_image"] = ""
                    else:
                        # Fallback to get_image_from_target if no published info
                        from routes.utils import get_image_from_target
                        current_image = get_image_from_target(publish_destination)
                        if current_image:
                            image_path = current_image.get("local_path", current_image.get("name", ""))
                            
                            # If this is a symlink or copy, try to resolve it
                            if image_path and os.path.islink(image_path):
                                resolved_path = os.path.realpath(image_path)
                                template_vars["_current_image"] = resolved_path
                                template_vars["current_image"] = resolved_path
                            else:
                                template_vars["_current_image"] = image_path
                                template_vars["current_image"] = image_path
                        else:
                            template_vars["_current_image"] = ""
                            template_vars["current_image"] = ""
                except Exception as e:
                    # If we can't get the current image, just set it to empty string
                    error(f"Error resolving _current_image for {publish_destination}: {str(e)}")
                    template_vars["_current_image"] = ""
                    template_vars["current_image"] = ""
            
            # Render the template with our variables
            result = template.render(**template_vars)
            
            return result
        except Exception as e:
            error(f"Error processing Jinja template: {str(e)}")
            # Return original if there's an error
            return value
    
    # For dictionaries, process both keys and values
    elif isinstance(value, dict):
        result = {}
        for k, v in value.items():
            # Process the key if it's a string
            if isinstance(k, str):
                processed_key = process_jinja_template(k, context, publish_destination)
            else:
                processed_key = k
            # Process the value
            processed_value = process_jinja_template(v, context, publish_destination)
            result[processed_key] = processed_value
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
    # DEBUG: Log for terminate instructions
    if instruction.get("action") == "terminate" and "test" in instruction:
        from utils.logger import info
        info(f"DEBUG PROCESS_JINJA: Processing terminate instruction with test='{instruction['test']}', conf_int={context.get('vars', {}).get('conf_int', 'NOT_FOUND')}")
        info(f"DEBUG PROCESS_JINJA: Full instruction before processing: {instruction}")
        info(f"DEBUG PROCESS_JINJA: Full context: {context}")
    
    # Add rate-limiting for debug logging
    if not hasattr(process_instruction_jinja, '_last_debug_log_time'):
        process_instruction_jinja._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    if publish_destination not in process_instruction_jinja._last_debug_log_time or \
       (current_time - process_instruction_jinja._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        process_instruction_jinja._last_debug_log_time[publish_destination] = current_time
    
    # Process the entire instruction recursively
    processed_instruction = process_jinja_template(instruction, context, publish_destination)
    
    # DEBUG: Log for terminate instructions after processing
    if instruction.get("action") == "terminate" and "test" in instruction:
        # DEBUG: Check if this is a terminate instruction with test field
        if isinstance(processed_instruction, dict) and processed_instruction.get("action") == "terminate":
            from utils.logger import info
            info(f"DEBUG INSTRUCTION: Processed terminate instruction, test field = '{processed_instruction.get('test', 'NO_TEST_FIELD')}'")
            info(f"DEBUG INSTRUCTION: Full processed instruction: {processed_instruction}")
    
    # Special handling for specific actions
    if isinstance(processed_instruction, dict) and "action" in processed_instruction:
        action = processed_instruction["action"]
        
        try:
            # Handle duration conversion for wait instruction
            if action == "wait" and "duration" in processed_instruction:
                # Make sure the duration is processed properly
                duration_input = processed_instruction["duration"]
                
                # Log the raw value for debugging
                if should_log:
                    debug(f"Processing wait duration: '{duration_input}' (type: {type(duration_input).__name__})")
                
                # Use our parse_duration utility to handle complex duration formats
                # DO NOT modify the original duration string - it's needed for display
                try:
                    # Parse duration but don't overwrite the original value
                    # Just ensure it's correctly parsed
                    from routes.scheduler_utils import parse_duration
                    parse_duration(duration_input)
                    if should_log:
                        debug(f"Successfully parsed duration: {duration_input}")
                except (ValueError, TypeError) as e:
                    error(f"Error parsing wait duration: {duration_input} - {str(e)}")
                    
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

def reset_trigger_execution_timestamps(publish_destination: str, allow_grace_period: bool = True) -> None:
    """
    Reset execution timestamps for the specified destination's triggers.
    Used when a schedule is updated while running to ensure triggers are properly evaluated.
    
    Args:
        publish_destination: The ID of the destination
        allow_grace_period: Whether to allow catching up on recent events through grace period.
                           Set to False when loading to avoid unexpected executions.
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

# Grace period (in seconds) during which a missed repeating trigger will still execute.
# Set to 5 minutes to avoid surprises after scheduler reloads while allowing quick catch-up
LATE_EXECUTION_GRACE_PERIOD_SECONDS = 5 * 60

# === Event Persistence Functions ===
    
# === Schedule Stack Management ===

def push_schedule(publish_destination: str, new_schedule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Push a new schedule onto the schedule stack and create a corresponding context.
    This preserves the existing schedule and context by pushing them onto their stacks,
    then creates a new context that inherits variables from the previous context.
    
    Args:
        publish_destination: The destination ID
        new_schedule: The new schedule to load on top of existing
        
    Returns:
        Dictionary with operation status and details
    """
    from routes.scheduler import start_scheduler
    
    debug(f"push_schedule: Starting for {publish_destination}")
    
    # Initialize stacks if they don't exist
    if publish_destination not in scheduler_schedule_stacks:
        scheduler_schedule_stacks[publish_destination] = []
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = []
    
    # Get current schedule and context (if any)
    current_schedule = None
    current_context = None
    
    if scheduler_schedule_stacks[publish_destination]:
        current_schedule = scheduler_schedule_stacks[publish_destination][-1]
        debug(f"push_schedule: Found existing schedule on stack")
    
    if scheduler_contexts_stacks[publish_destination]:
        current_context = scheduler_contexts_stacks[publish_destination][-1]
        debug(f"push_schedule: Found existing context with {len(current_context.get('vars', {}))} variables")
    
    # If we have an existing schedule and context, push them onto the stacks
    # (This makes them available to return to when the new schedule unloads)
    if current_schedule is not None:
        # The current schedule is already at the top of the stack, we just need to add the new one
        scheduler_schedule_stacks[publish_destination].append(new_schedule)
        debug(f"push_schedule: Pushed new schedule onto stack (stack size now: {len(scheduler_schedule_stacks[publish_destination])})")
    else:
        # No existing schedule, this is the first one
        scheduler_schedule_stacks[publish_destination] = [new_schedule]
        debug(f"push_schedule: Set first schedule on stack")
    
    # Create new context for the new schedule
    if current_context is not None:
        # Copy variables from current context to new context
        new_context = copy_context(current_context)
        debug(f"push_schedule: Created new context inheriting {len(new_context.get('vars', {}))} variables from current context")
    else:
        # No existing context, create default
        new_context = default_context()
        debug(f"push_schedule: Created new default context")
    
    # If we have an existing context, push it onto the context stack
    if current_context is not None:
        # Push the current context onto the stack before adding the new one
        scheduler_contexts_stacks[publish_destination].append(new_context)
        debug(f"push_schedule: Pushed new context onto stack (stack size now: {len(scheduler_contexts_stacks[publish_destination])})")
    else:
        # No existing context, this is the first one
        scheduler_contexts_stacks[publish_destination] = [new_context]
        debug(f"push_schedule: Set first context on stack")
    
    # Update scheduler state to persist the stacks
    update_scheduler_state(
        publish_destination,
        schedule_stack=scheduler_schedule_stacks[publish_destination],
        context_stack=scheduler_contexts_stacks[publish_destination],
        force_save=True
    )
    
    # Execute initial actions for the new schedule if it has any
    from routes.scheduler import resolve_schedule, run_instruction
    initial_instructions = resolve_schedule(new_schedule, datetime.now(), publish_destination, include_initial_actions=True, context=new_context)
    if initial_instructions:
        info(f"Executing initial actions for pushed schedule on {publish_destination}")
        # Get the current logs for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        
        # Process all instruction blocks from initial actions
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
                debug(f"Processing initial instruction block from {source}{flags_str}")
                
            for instr in block:
                try:
                    debug(f"push_schedule: About to execute initial instruction: {instr.get('action', 'unknown')}")
                    should_unload = run_instruction(instr, new_context, datetime.now(), scheduler_logs[publish_destination], publish_destination)
                    debug(f"push_schedule: Initial instruction {instr.get('action', 'unknown')} completed, should_unload={should_unload}")
                    if should_unload == "EXIT_BLOCK":
                        debug(f"EXIT_BLOCK signal received, breaking out of instruction block early")
                        break  # Exit the current instruction block
                    elif should_unload:
                        debug(f"Unload signal received during initial actions - this should not happen")
                        break
                except Exception as e:
                    error_msg = f"Error running initial instruction {instr.get('action', 'unknown')}: {str(e)}"
                    error(error_msg)
                    import traceback
                    error(f"Traceback: {traceback.format_exc()}")
                    scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        
        # Save context changes after running initial actions
        update_scheduler_state(
            publish_destination,
            schedule_stack=scheduler_schedule_stacks[publish_destination],
            context_stack=scheduler_contexts_stacks[publish_destination],
            force_save=True
        )
    
    # Log the operation
    now = datetime.now()
    if publish_destination in scheduler_logs:
        scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] Pushed new schedule onto stack (stack size: {len(scheduler_schedule_stacks[publish_destination])})")
    
    debug(f"push_schedule: Successfully pushed schedule, final stack size: {len(scheduler_schedule_stacks[publish_destination])}")
    
    return {
        "status": "success",
        "message": "Schedule pushed onto stack",
        "stack_size": len(scheduler_schedule_stacks[publish_destination]),
        "context_inherited_vars": len(new_context.get('vars', {}))
    }

def pop_schedule(publish_destination: str) -> Dict[str, Any]:
    """
    Pop the top schedule from the schedule stack and restore the previous context.
    This removes the current schedule and restores the previous schedule and context.
    
    Args:
        publish_destination: The destination ID
        
    Returns:
        Dictionary with operation status and details
    """
    debug(f"pop_schedule: Starting for {publish_destination}")
    
    # Check if we have schedules to pop
    if (publish_destination not in scheduler_schedule_stacks or 
        not scheduler_schedule_stacks[publish_destination]):
        return {
            "status": "error",
            "message": "No schedules on stack to pop"
        }
    
    # Check if the current schedule has prevent_unload=true
    # BUT: If this is a self-unload (schedule unloading itself), allow it even if previous schedule is protected
    current_schedule = scheduler_schedule_stacks[publish_destination][-1]
    if current_schedule.get("prevent_unload", False):
        return {
            "status": "error", 
            "message": "Current schedule cannot be unloaded (prevent_unload=true)"
        }
    
    # Pop the current schedule
    popped_schedule = scheduler_schedule_stacks[publish_destination].pop()
    debug(f"pop_schedule: Popped schedule from stack (stack size now: {len(scheduler_schedule_stacks[publish_destination])})")
    
    # Pop the current context 
    if (publish_destination in scheduler_contexts_stacks and 
        scheduler_contexts_stacks[publish_destination]):
        popped_context = scheduler_contexts_stacks[publish_destination].pop()
        debug(f"pop_schedule: Popped context from stack (stack size now: {len(scheduler_contexts_stacks[publish_destination])})")
    else:
        debug(f"pop_schedule: Warning - no context stack found to pop")
    
    # Update scheduler state to persist the stacks
    update_scheduler_state(
        publish_destination,
        schedule_stack=scheduler_schedule_stacks[publish_destination],
        context_stack=scheduler_contexts_stacks[publish_destination],
        force_save=True
    )
    
    # Log the operation
    now = datetime.now()
    if publish_destination in scheduler_logs:
        scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] Popped schedule from stack (stack size: {len(scheduler_schedule_stacks[publish_destination])})")
    
    # If no more schedules, stop the scheduler
    if not scheduler_schedule_stacks[publish_destination]:
        from routes.scheduler import stop_scheduler
        stop_scheduler(publish_destination)
        debug(f"pop_schedule: No more schedules, stopped scheduler")
        if publish_destination in scheduler_logs:
            scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] No schedules remaining, stopped scheduler")
        
        return {
            "status": "success",
            "message": "Popped last schedule and stopped scheduler",
            "stack_size": 0,
            "scheduler_stopped": True
        }
    
    debug(f"pop_schedule: Successfully popped schedule, remaining stack size: {len(scheduler_schedule_stacks[publish_destination])}")
    
    return {
        "status": "success", 
        "message": "Schedule popped from stack",
        "stack_size": len(scheduler_schedule_stacks[publish_destination]),
        "scheduler_stopped": False
    }

def load_schedule_on_stack(publish_destination: str, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Load a new schedule on top of any existing schedule using proper stack management.
    This is the main entry point for loading schedules that should stack properly.
    
    Args:
        publish_destination: The destination ID
        schedule: The schedule to load
        
    Returns:
        Dictionary with operation status and details
    """
    from routes.scheduler import start_scheduler, scheduler_states, running_schedulers
    
    info(f"load_schedule_on_stack: Loading schedule for {publish_destination}")
    
    try:
        # Initialize scheduler logs if needed
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        
        # Validate the schedule structure
        if not isinstance(schedule, dict):
            return {
                "status": "error",
                "message": "Schedule must be a dictionary"
            }
        
        # Push the new schedule onto the stack
        push_result = push_schedule(publish_destination, schedule)
        if push_result["status"] != "success":
            return push_result
        
        # Check if scheduler is already running
        scheduler_was_running = (publish_destination in running_schedulers and 
                               publish_destination in scheduler_states and
                               scheduler_states[publish_destination] == "running")
        
        if scheduler_was_running:
            info(f"load_schedule_on_stack: Scheduler already running for {publish_destination}, new schedule will execute on existing scheduler")
            # The running scheduler will automatically pick up the new schedule from the top of the stack
        else:
            info(f"load_schedule_on_stack: Starting new scheduler for {publish_destination}")
            # Start the scheduler with the new schedule (top of stack)
            start_scheduler(publish_destination, schedule)
        
        return {
            "status": "success",
            "message": "Schedule loaded on stack successfully",
            "stack_size": push_result["stack_size"],
            "scheduler_was_running": scheduler_was_running,
            "inherited_vars": push_result["context_inherited_vars"]
        }
        
    except Exception as e:
        error_msg = f"Error loading schedule on stack: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        
        if publish_destination in scheduler_logs:
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        
        return {
            "status": "error",
            "message": error_msg
        }

def unload_schedule_from_stack(publish_destination: str) -> Dict[str, Any]:
    """
    Unload the current schedule from the stack and restore the previous one.
    This is the main entry point for unloading schedules with proper stack management.
    
    Args:
        publish_destination: The destination ID
        
    Returns:
        Dictionary with operation status and details
    """
    info(f"unload_schedule_from_stack: Unloading schedule for {publish_destination}")
    
    try:
        # Initialize scheduler logs if needed
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        
        # Pop the schedule from the stack
        pop_result = pop_schedule(publish_destination)
        
        return pop_result
        
    except Exception as e:
        error_msg = f"Error unloading schedule from stack: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        
        if publish_destination in scheduler_logs:
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        
        return {
            "status": "error", 
            "message": error_msg
        }
    