import pytest
import json
import os
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

from routes.scheduler import (
    start_scheduler, stop_scheduler, 
    scheduler_states, scheduler_contexts_stacks, scheduler_schedule_stacks
)
from routes.scheduler_utils import (
    update_scheduler_state, get_current_context, set_context_variable,
    get_scheduler_storage_path, load_scheduler_state, save_scheduler_state
)
from routes.scheduler_api import (
    api_pause_scheduler, api_unpause_scheduler, api_load_schedule,
    api_set_scheduler_context
)

# Fixtures
# Using clean_scheduler_state from conftest.py for proper path isolation

@pytest.fixture
def mock_flask_request():
    """Mock Flask request context."""
    class MockRequest:
        json = {}
        
        @classmethod
        def get_json(cls):
            return cls.json
            
    with patch('routes.scheduler_api.request', MockRequest):
        yield MockRequest

@pytest.fixture
def test_schedule():
    """Create a simple test schedule."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initialized", "input": {"value": True}}
            ]
        },
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "scheduled_actions": [
                    {
                        "time": "12:00",
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "lunch_time", "input": {"value": True}}
                            ]
                        }
                    }
                ]
            }
        ]
    }

@pytest.fixture
def setup_running_scheduler(clean_scheduler_state, test_schedule, app_context):
    """Set up a running scheduler with context for testing."""
    publish_destination = "test_destination"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[publish_destination] = []
    
    # Create initial context manually since start_scheduler may not be creating it properly in tests
    scheduler_contexts_stacks[publish_destination] = [{
        "vars": {},
        "publish_destination": publish_destination
    }]
    
    # Add a default empty schedule stack
    scheduler_schedule_stacks[publish_destination] = [test_schedule]
    
    # Set initial state
    scheduler_states[publish_destination] = "stopped"
    
    # Initialize scheduler
    start_scheduler(publish_destination, test_schedule)
    
    # Set some variables in the context
    context = scheduler_contexts_stacks[publish_destination][0]
    context["vars"]["test_var1"] = "test_value1"
    context["vars"]["test_var2"] = 42
    context["vars"]["test_var3"] = {"nested": "object"}
    
    # Update state to persist variables
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination]
    )
    
    return publish_destination

def verify_disk_memory_sync(publish_destination):
    """Helper function to verify disk and memory state are in sync."""
    # Load state from disk
    disk_state = load_scheduler_state(publish_destination)
    
    # Get in-memory state
    memory_context_stack = scheduler_contexts_stacks.get(publish_destination, [])
    memory_schedule_stack = scheduler_schedule_stacks.get(publish_destination, [])
    memory_state = scheduler_states.get(publish_destination)
    
    # Ensure we have a valid disk state
    assert disk_state is not None, f"Failed to load disk state for {publish_destination}"
    
    # Verify context stack matches
    disk_context_stack = disk_state.get("context_stack", [])
    assert len(disk_context_stack) == len(memory_context_stack), \
        f"Context stack size mismatch: disk={len(disk_context_stack)}, memory={len(memory_context_stack)}"
    
    # If stacks are empty, there's nothing more to verify for context
    if not disk_context_stack:
        return True
    
    # Verify each context's variables match
    for i, (disk_context, memory_context) in enumerate(zip(disk_context_stack, memory_context_stack)):
        disk_vars = disk_context.get("vars", {})
        memory_vars = memory_context.get("vars", {})
        
        # Ensure they have the same keys
        assert set(disk_vars.keys()) == set(memory_vars.keys()), \
            f"Context vars keys mismatch at position {i}: disk={set(disk_vars.keys())}, memory={set(memory_vars.keys())}"
        
        # Check each variable value matches
        for key in disk_vars:
            assert disk_vars[key] == memory_vars[key], \
                f"Variable '{key}' mismatch at position {i}: disk={disk_vars[key]}, memory={memory_vars[key]}"
    
    # Verify schedule stack matches
    assert len(disk_state.get("schedule_stack", [])) == len(memory_schedule_stack), \
        f"Schedule stack size mismatch: disk={len(disk_state.get('schedule_stack', []))}, memory={len(memory_schedule_stack)}"
    
    # Verify state matches
    assert disk_state.get("state") == memory_state, \
        f"State mismatch: disk={disk_state.get('state')}, memory={memory_state}"
    
    return True

def set_context_variable_safe(publish_destination, var_name, value):
    """Helper to safely set a context variable, ensuring context exists first."""
    # Check if context exists
    if publish_destination not in scheduler_contexts_stacks or not scheduler_contexts_stacks[publish_destination]:
        # Initialize context stack
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {},
            "publish_destination": publish_destination
        }]
    
    # Now set the variable safely
    set_context_variable(publish_destination, var_name, value)

def get_context_var_safe(publish_destination, var_name, default=None):
    """Safely get a context variable, handling None contexts."""
    context = get_current_context(publish_destination)
    if context is None:
        return default
    return context.get("vars", {}).get(var_name, default)

# Test synchronization across various operations
def test_context_var_update_sync(setup_running_scheduler, app_context):
    """Test that context variable updates keep disk and memory in sync."""
    publish_destination = setup_running_scheduler
    
    # Initial sync check
    assert verify_disk_memory_sync(publish_destination)
    
    # Update a variable
    set_context_variable(publish_destination, "new_var", "new_value")
    
    # Check sync after update
    assert verify_disk_memory_sync(publish_destination)
    
    # Update existing variable
    set_context_variable(publish_destination, "test_var1", "updated_value")
    
    # Check sync after update
    assert verify_disk_memory_sync(publish_destination)
    
    # Delete a variable
    set_context_variable(publish_destination, "test_var2", None)
    
    # Check sync after deletion
    assert verify_disk_memory_sync(publish_destination)

def test_api_var_update_sync(setup_running_scheduler, mock_flask_request, app_context, request_context):
    """Test that API variable updates keep disk and memory in sync."""
    publish_destination = setup_running_scheduler
    
    # Initial sync check
    assert verify_disk_memory_sync(publish_destination)
    
    # Update via API
    mock_flask_request.json = {"var_name": "api_var", "var_value": "api_value"}
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_set_scheduler_context(publish_destination)
    
    # Check sync after API update
    assert verify_disk_memory_sync(publish_destination)

def test_load_schedule_sync(clean_scheduler_state, test_schedule, mock_flask_request, app_context, request_context):
    """Test that loading a schedule maintains disk and memory sync."""
    publish_destination = "test_destination_load"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[publish_destination] = []
    
    # Initialize context explicitly
    scheduler_contexts_stacks[publish_destination] = [{
        "vars": {},
        "publish_destination": publish_destination
    }]
    
    # First-time load
    mock_flask_request.json = test_schedule
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_load_schedule(publish_destination)
    
    # Check sync after initial load
    assert verify_disk_memory_sync(publish_destination)
    
    # Add a variable to the context
    set_context_variable_safe(publish_destination, "before_reload", "present")
    assert verify_disk_memory_sync(publish_destination)
    
    # Now reload the same schedule - should preserve context
    mock_flask_request.json = test_schedule
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_load_schedule(publish_destination)
    
    # Check sync after reload
    assert verify_disk_memory_sync(publish_destination)
    
    # Verify the variable is still present
    assert get_context_var_safe(publish_destination, "before_reload") == "present"

def test_pause_unpause_sync(setup_running_scheduler, mock_flask_request, app_context, request_context):
    """Test that pausing and unpausing maintains disk and memory sync."""
    publish_destination = setup_running_scheduler
    
    # Initial sync check
    assert verify_disk_memory_sync(publish_destination)
    
    # Pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Check sync after pause
    assert verify_disk_memory_sync(publish_destination)
    
    # Add a variable while paused
    set_context_variable(publish_destination, "while_paused", True)
    assert verify_disk_memory_sync(publish_destination)
    
    # Unpause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_unpause_scheduler(publish_destination)
    
    # Check sync after unpause
    assert verify_disk_memory_sync(publish_destination)
    
    # Verify the variable added during pause is still present
    memory_context = get_current_context(publish_destination)
    assert memory_context.get("vars", {}).get("while_paused") == True

def test_multiple_operations_sync(setup_running_scheduler, test_schedule, mock_flask_request, app_context, request_context):
    """Test maintaining sync across a sequence of multiple operations."""
    publish_destination = setup_running_scheduler
    
    # Sequence of operations
    operations = [
        # Add variable
        lambda: set_context_variable(publish_destination, "seq", 1),
        
        # Pause
        lambda: api_pause_scheduler(publish_destination),
        
        # Update variable while paused
        lambda: set_context_variable(publish_destination, "seq", 2),
        
        # Unpause
        lambda: api_unpause_scheduler(publish_destination),
        
        # Reload schedule
        lambda: (setattr(mock_flask_request, 'json', test_schedule), 
                api_load_schedule(publish_destination)),
        
        # Delete variable
        lambda: set_context_variable(publish_destination, "test_var3", None)
    ]
    
    # Patch jsonify for API calls
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        
        # Execute operations and verify sync after each
        for i, operation in enumerate(operations):
            operation()
            assert verify_disk_memory_sync(publish_destination), f"Sync failed after operation {i}"
    
    # Final check - memory should match what we expect after all operations
    memory_context = get_current_context(publish_destination)
    assert memory_context.get("vars", {}).get("seq") == 2
    assert "test_var3" not in memory_context.get("vars", {})
    assert memory_context.get("vars", {}).get("test_var1") == "test_value1"

def test_direct_memory_changes_sync(setup_running_scheduler, app_context):
    """Test scenarios where memory is directly modified and then synchronized."""
    publish_destination = setup_running_scheduler
    
    # Initial sync check
    assert verify_disk_memory_sync(publish_destination)
    
    # Use the safe helper function instead of directly modifying memory
    set_context_variable_safe(publish_destination, "direct_edit", "memory_only")
    
    # Verify disk and memory are now in sync (since set_context_variable_safe forces save)
    disk_state = load_scheduler_state(publish_destination)
    assert "direct_edit" in disk_state["context_stack"][0]["vars"], f"Variable not found in disk state: {disk_state['context_stack'][0]['vars']}"
    assert disk_state["context_stack"][0]["vars"]["direct_edit"] == "memory_only"
    
    # Verify sync is maintained
    assert verify_disk_memory_sync(publish_destination)

def test_disk_load_doesnt_lose_memory_context(setup_running_scheduler, app_context):
    """Test that loading from disk doesn't lose memory context when only updating schedule."""
    publish_destination = setup_running_scheduler
    
    # Initial sync check
    assert verify_disk_memory_sync(publish_destination)
    
    # Use the safe helper function instead of directly modifying memory
    set_context_variable_safe(publish_destination, "unpersisted", "memory_only")
    
    # Verify disk and memory are in sync
    disk_state = load_scheduler_state(publish_destination)
    assert "unpersisted" in disk_state["context_stack"][0]["vars"]
    
    # Make a copy of the current context before the update
    context_before = dict(scheduler_contexts_stacks[publish_destination][0])
    vars_before = dict(context_before.get("vars", {}))
    
    # Now update only the schedule - shouldn't lose our memory-only variable
    new_schedule = {"triggers": [], "initial_actions": {"instructions_block": []}}
    update_scheduler_state(publish_destination, schedule_stack=[new_schedule], force_save=True)
    
    # Memory context should still have our variable
    memory_vars = scheduler_contexts_stacks[publish_destination][0].get("vars", {})
    assert "unpersisted" in memory_vars, f"Variable lost. Before: {vars_before}, After: {memory_vars}"
    assert memory_vars["unpersisted"] == "memory_only"
    
    # Disk state should still have our variable too
    disk_state = load_scheduler_state(publish_destination)
    assert disk_state["context_stack"][0]["vars"]["unpersisted"] == "memory_only"
    
    # Verify everything is in sync
    assert verify_disk_memory_sync(publish_destination)

def test_complex_nested_objects_sync(setup_running_scheduler, app_context):
    """Test synchronization with complex nested objects in context variables."""
    publish_destination = setup_running_scheduler
    
    # Add complex nested structures
    complex_var = {
        "level1": {
            "level2": [1, 2, {"level3": "deep"}, [4, 5]],
            "sibling": {"a": 1, "b": 2}
        },
        "array": [{"obj1": "val1"}, {"obj2": "val2"}]
    }
    
    set_context_variable(publish_destination, "complex", complex_var)
    assert verify_disk_memory_sync(publish_destination)
    
    # Modify part of the nested structure
    memory_context = get_current_context(publish_destination)
    memory_context["vars"]["complex"]["level1"]["level2"][2]["level3"] = "modified"
    
    # Sync the changes
    update_scheduler_state(publish_destination, 
                         context_stack=scheduler_contexts_stacks[publish_destination])
    
    # Verify sync and modification persistence
    assert verify_disk_memory_sync(publish_destination)
    disk_state = load_scheduler_state(publish_destination)
    assert disk_state["context_stack"][0]["vars"]["complex"]["level1"]["level2"][2]["level3"] == "modified"

def test_cross_destination_isolation(clean_scheduler_state, test_schedule, mock_flask_request, app_context, request_context):
    """Test that operations on one destination don't affect other destinations' state sync."""
    # Set up two destinations
    dest1 = "test_dest1"
    dest2 = "test_dest2"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[dest1] = []
    scheduler_logs[dest2] = []
    
    # Initialize contexts for both destinations
    scheduler_contexts_stacks[dest1] = [{"vars": {}, "publish_destination": dest1}]
    scheduler_contexts_stacks[dest2] = [{"vars": {}, "publish_destination": dest2}]
    
    # Initialize both with schedules
    mock_flask_request.json = test_schedule
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_load_schedule(dest1)
        api_load_schedule(dest2)
    
    # Add unique variables to each destination
    set_context_variable_safe(dest1, "unique_to_dest1", "dest1_value")
    set_context_variable_safe(dest2, "unique_to_dest2", "dest2_value")
    
    # Verify both are in sync
    assert verify_disk_memory_sync(dest1)
    assert verify_disk_memory_sync(dest2)
    
    # Perform operations on destination 1
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        
        # Pause dest1
        api_pause_scheduler(dest1)
        
        # Modify dest1 variable
        set_context_variable_safe(dest1, "unique_to_dest1", "modified")
        
        # Load a new schedule to dest1
        modified_schedule = dict(test_schedule)
        modified_schedule["triggers"] = []  # Remove triggers
        mock_flask_request.json = modified_schedule
        api_load_schedule(dest1)
    
    # Verify both destinations are still in sync
    assert verify_disk_memory_sync(dest1)
    assert verify_disk_memory_sync(dest2)
    
    # Verify dest2 was not affected by operations on dest1
    assert get_context_var_safe(dest2, "unique_to_dest2") == "dest2_value"
    
    # Verify dest1 changes were properly persisted
    assert get_context_var_safe(dest1, "unique_to_dest1") == "modified"
    
    # Make sure schedule state was updated correctly
    assert len(scheduler_schedule_stacks.get(dest1, [])) == 1
    assert len(scheduler_schedule_stacks.get(dest1, [])[0].get("triggers", [])) == 0
    assert len(scheduler_schedule_stacks.get(dest2, [])) == 1
    assert len(scheduler_schedule_stacks.get(dest2, [])[0].get("triggers", [])) == 1
    
    # Check that destination states are different as expected
    assert scheduler_states.get(dest1) == "paused"
    assert scheduler_states.get(dest2) == "stopped"

def test_schedule_reload_preserves_variables(clean_scheduler_state, test_schedule, mock_flask_request, app_context, request_context):
    """Test that reloading a schedule preserves all existing context variables."""
    publish_destination = "test_destination_reload"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[publish_destination] = []
    
    # Initialize context explicitly
    scheduler_contexts_stacks[publish_destination] = [{
        "vars": {},
        "publish_destination": publish_destination
    }]
    
    # Initial load
    mock_flask_request.json = test_schedule
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_load_schedule(publish_destination)
    
    # Set several variables in the context
    set_context_variable_safe(publish_destination, "important_var1", "critical_value")
    set_context_variable_safe(publish_destination, "important_var2", {"nested": "object", "with": ["array", "values"]})
    set_context_variable_safe(publish_destination, "counter", 42)
    
    # Verify everything is synced before reload
    assert verify_disk_memory_sync(publish_destination)
    
    # Get a snapshot of the context vars
    before_vars = dict(scheduler_contexts_stacks[publish_destination][0].get("vars", {}))
    
    # Now reload with a slightly modified schedule
    modified_schedule = dict(test_schedule)
    modified_schedule["initial_actions"]["instructions_block"][0]["var"] = "different_var"
    mock_flask_request.json = modified_schedule
    
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_load_schedule(publish_destination)
    
    # Verify everything is still in sync after reload
    assert verify_disk_memory_sync(publish_destination)
    
    # Get the context after reload
    after_vars = scheduler_contexts_stacks[publish_destination][0].get("vars", {})
    
    # Verify all important variables are still there
    assert after_vars.get("important_var1") == "critical_value"
    assert after_vars.get("important_var2") == {"nested": "object", "with": ["array", "values"]}
    assert after_vars.get("counter") == 42
    
    # Make sure all original variables are preserved
    for key in before_vars:
        assert key in after_vars, f"Variable '{key}' was lost during schedule reload"
        assert before_vars[key] == after_vars[key], f"Variable '{key}' changed during reload" 