import pytest
import json
import os
from datetime import datetime
from unittest.mock import patch, MagicMock, mock_open

from routes.scheduler_utils import (
    update_scheduler_state, scheduler_contexts_stacks, scheduler_schedule_stacks, scheduler_states,
    get_current_context, push_context, pop_context,
    save_scheduler_state, load_scheduler_state
)
from routes.scheduler_api import (
    api_set_scheduler_context, api_get_scheduler_context
)

# Define helper functions to manage variables since they're not directly in scheduler_utils.py
def set_var(publish_destination, var_name, var_value):
    """Helper function to set a variable in the current context."""
    context = get_current_context(publish_destination)
    if not context:
        return None
    if "vars" not in context:
        context["vars"] = {}
    context["vars"][var_name] = var_value
    return var_value

def get_var(publish_destination, var_name):
    """Helper function to get a variable from the current context."""
    context = get_current_context(publish_destination)
    if not context or "vars" not in context:
        return None
    return context["vars"].get(var_name)

def remove_var(publish_destination, var_name):
    """Helper function to remove a variable from the current context."""
    context = get_current_context(publish_destination)
    if not context or "vars" not in context:
        return False
    if var_name in context["vars"]:
        del context["vars"][var_name]
        return True
    return False

@pytest.fixture
def multi_context_setup(clean_scheduler_state):
    """Set up multiple publish destinations with contexts."""
    destinations = ["test_dest1", "test_dest2"]
    
    for dest in destinations:
        # Initialize context stack
        scheduler_contexts_stacks[dest] = [{"vars": {}, "publish_destination": dest}]
        
        # Add some test variables
        context = scheduler_contexts_stacks[dest][0]
        context["vars"]["test_var"] = f"value_for_{dest}"
        context["vars"]["shared_var"] = "shared_value"
        
        # Persist to disk
        update_scheduler_state(dest, context_stack=scheduler_contexts_stacks[dest])
    
    return destinations

@pytest.fixture
def mock_flask_request():
    """Mock Flask request context for API calls."""
    with patch('routes.scheduler_api.request') as mock_request:
        mock_request.json = {}
        mock_request.get_json = lambda: mock_request.json
        yield mock_request

@pytest.fixture
def clean_scheduler_state():
    """Clean up scheduler state before and after tests."""
    # Store original state
    original_states = dict(scheduler_states) if 'scheduler_states' in globals() else {}
    original_contexts = {k: v[:] for k, v in scheduler_contexts_stacks.items()}
    original_schedules = {k: v[:] for k, v in scheduler_schedule_stacks.items()}
    
    # Clear state for test
    scheduler_contexts_stacks.clear()
    scheduler_schedule_stacks.clear()
    
    yield
    
    # Restore original state
    scheduler_contexts_stacks.clear()
    scheduler_contexts_stacks.update(original_contexts)
    
    scheduler_schedule_stacks.clear()
    scheduler_schedule_stacks.update(original_schedules)
    
    if 'scheduler_states' in globals():
        scheduler_states.clear()
        scheduler_states.update(original_states)

def test_variable_persistence(clean_scheduler_state):
    """Test that variables are properly persisted to disk."""
    publish_destination = "test_dest_persistence"
    
    # Initialize context
    context = {"vars": {}, "publish_destination": publish_destination}
    scheduler_contexts_stacks[publish_destination] = [context]
    
    # Set variables
    context["vars"]["var1"] = "value1"
    context["vars"]["var2"] = 42
    context["vars"]["var3"] = {"nested": "object"}
    
    # Persist to disk
    update_scheduler_state(publish_destination, context_stack=scheduler_contexts_stacks[publish_destination])
    
    # Verify in-memory state
    current_context = get_current_context(publish_destination)
    assert current_context["vars"]["var1"] == "value1"
    assert current_context["vars"]["var2"] == 42
    assert current_context["vars"]["var3"] == {"nested": "object"}
    
    # Clear memory state
    scheduler_contexts_stacks.clear()
    
    # Load from disk
    load_scheduler_state(publish_destination)
    
    # Verify loaded state matches what was saved
    loaded_context = get_current_context(publish_destination)
    assert loaded_context["vars"]["var1"] == "value1"
    assert loaded_context["vars"]["var2"] == 42
    assert loaded_context["vars"]["var3"] == {"nested": "object"}

def test_context_api_integration(clean_scheduler_state, mock_flask_request):
    """Test that API endpoints for context management correctly persist variables."""
    publish_destination = "test_dest_api"
    
    # Initialize context
    context = {"vars": {}, "publish_destination": publish_destination}
    scheduler_contexts_stacks[publish_destination] = [context]
    
    # Set a variable through the API
    mock_flask_request.json = {"var_name": "api_var", "var_value": "api_value"}
    with patch('routes.scheduler_api.jsonify') as mock_jsonify, \
         patch('routes.scheduler_api.request', mock_flask_request):
        mock_jsonify.return_value = {}
        api_set_scheduler_context(publish_destination)
    
    # Verify in-memory state
    current_context = get_current_context(publish_destination)
    assert current_context["vars"]["api_var"] == "api_value"
    
    # Clear memory state
    scheduler_contexts_stacks.clear()
    
    # Load from disk
    load_scheduler_state(publish_destination)
    
    # Verify loaded state includes the API-set variable
    loaded_context = get_current_context(publish_destination)
    assert loaded_context["vars"]["api_var"] == "api_value"

def test_update_without_context_preserves_vars(clean_scheduler_state):
    """Test that calling update_scheduler_state without context_stack preserves existing variables."""
    publish_destination = "test_dest_update"
    
    # Initialize context with variables
    context = {"vars": {"existing_var": "existing_value"}, "publish_destination": publish_destination}
    scheduler_contexts_stacks[publish_destination] = [context]
    
    # Persist to disk
    update_scheduler_state(publish_destination, context_stack=scheduler_contexts_stacks[publish_destination])
    
    # Add a new schedule without explicitly passing context
    test_schedule = {"triggers": [], "initial_actions": {}}
    update_scheduler_state(publish_destination, schedule_stack=[test_schedule])
    
    # Verify in-memory state still has the variable
    current_context = get_current_context(publish_destination)
    assert current_context["vars"]["existing_var"] == "existing_value"
    
    # Clear memory state
    scheduler_contexts_stacks.clear()
    
    # Load from disk
    load_scheduler_state(publish_destination)
    
    # Verify loaded state still has the variable
    loaded_context = get_current_context(publish_destination)
    assert loaded_context["vars"]["existing_var"] == "existing_value"

def test_set_get_var_in_different_contexts(multi_context_setup):
    """Test setting and getting variables in different contexts doesn't interfere."""
    destinations = multi_context_setup
    
    # Set variables in first destination
    set_var(destinations[0], "var1", "value1_dest1")
    set_var(destinations[0], "var2", "value2_dest1")
    
    # Set variables in second destination
    set_var(destinations[1], "var1", "value1_dest2")
    set_var(destinations[1], "var3", "value3_dest2")
    
    # Check variables in first destination
    assert get_var(destinations[0], "var1") == "value1_dest1"
    assert get_var(destinations[0], "var2") == "value2_dest1"
    assert get_var(destinations[0], "var3") is None  # Should not exist
    
    # Check variables in second destination
    assert get_var(destinations[1], "var1") == "value1_dest2"  # Different value
    assert get_var(destinations[1], "var2") is None  # Should not exist
    assert get_var(destinations[1], "var3") == "value3_dest2"
    
    # Check initial vars still exist in both
    assert get_var(destinations[0], "test_var") == "value_for_test_dest1"
    assert get_var(destinations[1], "test_var") == "value_for_test_dest2"

def test_remove_var_in_different_contexts(multi_context_setup):
    """Test removing variables in one context doesn't affect other contexts."""
    destinations = multi_context_setup
    
    # Set same variable in both destinations
    set_var(destinations[0], "shared_var", "value_dest1")
    set_var(destinations[1], "shared_var", "value_dest2")
    
    # Remove variable from first destination
    remove_var(destinations[0], "shared_var")
    
    # Variable should be gone from first destination
    assert get_var(destinations[0], "shared_var") is None
    
    # But still exist in second destination
    assert get_var(destinations[1], "shared_var") == "value_dest2"
    
    # Remove variable from second destination
    remove_var(destinations[1], "shared_var")
    
    # Now should be gone from second destination too
    assert get_var(destinations[1], "shared_var") is None

def test_push_pop_context_with_vars(multi_context_setup):
    """Test pushing and popping contexts with variables."""
    destinations = multi_context_setup
    dest = destinations[0]
    
    # Create a new context with variables
    new_context = {
        "vars": {
            "new_var1": "new_value1",
            "new_var2": "new_value2"
        },
        "publish_destination": dest
    }
    
    # Push the new context
    push_context(dest, new_context)
    
    # Check stack length
    assert len(scheduler_contexts_stacks[dest]) == 2
    
    # Check current context has the new variables
    current_context = get_current_context(dest)
    assert current_context["vars"]["new_var1"] == "new_value1"
    assert current_context["vars"]["new_var2"] == "new_value2"
    
    # Initial var from previous context should not be directly accessible
    assert "test_var" not in current_context["vars"]
    
    # Pop the context
    popped_context = pop_context(dest)
    
    # Check the popped context is the one we pushed
    assert popped_context["vars"]["new_var1"] == "new_value1"
    assert popped_context["vars"]["new_var2"] == "new_value2"
    
    # Check we're back to the original context
    current_context = get_current_context(dest)
    assert current_context["vars"]["test_var"] == "value_for_test_dest1"
    assert "new_var1" not in current_context["vars"]

def test_state_persistence_with_context_vars(multi_context_setup, tmpdir):
    """Test that state persistence correctly saves and loads context with variables."""
    destinations = multi_context_setup
    dest = destinations[0]
    
    # Set some variables
    set_var(dest, "test_var1", "test_value1")
    set_var(dest, "test_var2", "test_value2")
    
    # Create a temp file for state
    state_file = os.path.join(tmpdir, f"{dest}_state.json")
    
    # Mock the storage path
    with patch('routes.scheduler_utils.get_scheduler_storage_path', return_value=state_file):
        # Save the state
        save_scheduler_state(dest)
        
        # Modify the vars to verify they get restored
        scheduler_contexts_stacks[dest][0]["vars"]["test_var1"] = "modified_value"
        scheduler_contexts_stacks[dest][0]["vars"].pop("test_var2")
        
        # Load the state back
        load_scheduler_state(dest)
        
        # Verify the variables were restored
        assert get_var(dest, "test_var1") == "test_value1"
        assert get_var(dest, "test_var2") == "test_value2"
        assert get_var(dest, "test_var") == "value_for_test_dest1"

def test_update_scheduler_state_preserves_vars(multi_context_setup):
    """Test that update_scheduler_state preserves variables when called with different params."""
    destinations = multi_context_setup
    dest = destinations[0]
    
    # Set additional vars
    set_var(dest, "important_var", "important_value")
    
    # Get the current context stack
    context_stack = scheduler_contexts_stacks[dest]
    
    # Create empty schedule stack
    schedule_stack = []
    
    # Update with just the schedule (should preserve context vars)
    update_scheduler_state(dest, schedule_stack=schedule_stack)
    
    # Verify vars are preserved
    assert get_var(dest, "important_var") == "important_value"
    assert get_var(dest, "test_var") == "value_for_test_dest1"
    
    # Update with just state
    update_scheduler_state(dest, state="running")
    
    # Verify vars are still preserved
    assert get_var(dest, "important_var") == "important_value"
    assert get_var(dest, "test_var") == "value_for_test_dest1"

def test_cross_context_operations_dont_interfere(multi_context_setup):
    """Test operations across multiple contexts don't interfere with each other."""
    destinations = multi_context_setup
    
    # Set vars in both contexts
    for i, dest in enumerate(destinations):
        for j in range(5):
            set_var(dest, f"var_{j}", f"value_{i}_{j}")
    
    # Perform a sequence of operations
    set_var(destinations[0], "special_var", "special_value")
    remove_var(destinations[1], "var_2")
    
    # Push context to first destination
    push_context(destinations[0], {
        "vars": {"pushed_var": "pushed_value"},
        "publish_destination": destinations[0]
    })
    
    # Update state for second destination
    update_scheduler_state(destinations[1], state="running")
    
    # Verify first destination has its pushed context
    assert get_var(destinations[0], "pushed_var") == "pushed_value"
    
    # Verify second destination still has its vars except the removed one
    assert get_var(destinations[1], "var_0") == f"value_1_0"
    assert get_var(destinations[1], "var_1") == f"value_1_1"
    assert get_var(destinations[1], "var_2") is None  # This was removed
    assert get_var(destinations[1], "var_3") == f"value_1_3"
    
    # Pop context from first destination
    pop_context(destinations[0])
    
    # Verify first destination has its original vars
    for j in range(5):
        assert get_var(destinations[0], f"var_{j}") == f"value_0_{j}"
    assert get_var(destinations[0], "special_var") == "special_value" 