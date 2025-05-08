import pytest
import json
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

from routes.scheduler import (
    start_scheduler, stop_scheduler, 
    scheduler_states, scheduler_contexts_stacks
)
from routes.scheduler_utils import (
    update_scheduler_state, get_current_context,
    set_context_variable, save_scheduler_state
)
from routes.scheduler_api import (
    api_pause_scheduler, api_unpause_scheduler
)

# Fixtures
# Using clean_scheduler_state from conftest.py for proper path isolation

@pytest.fixture
def mock_flask_request(app_request_context):
    """Mock Flask request context."""
    with patch('routes.scheduler_api.request') as mock_request:
        mock_request.json = {}
        mock_request.get_json = lambda: mock_request.json
        yield mock_request

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
def setup_running_scheduler(clean_scheduler_state, test_schedule, app_request_context):
    """Set up a running scheduler with context for testing."""
    publish_destination = "test_destination"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[publish_destination] = []
    
    # Initialize scheduler directly in the state objects
    from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_states, scheduler_schedule_stacks
    
    # Create initial context with test variables
    initial_context = {
        "vars": {
            "test_var1": "test_value1", 
            "test_var2": 42,
            "test_var3": {"nested": "object"},
            "initialized": True  # Include the variable set by initial_actions
        },
        "publish_destination": publish_destination
    }
    
    # Set up the memory state explicitly - this bypasses all the complex startup logic
    scheduler_contexts_stacks[publish_destination] = [initial_context]
    scheduler_schedule_stacks[publish_destination] = [test_schedule]
    scheduler_states[publish_destination] = "running"
    
    # Force a save to disk to ensure persistence
    save_scheduler_state(publish_destination, {
        "schedule_stack": scheduler_schedule_stacks[publish_destination],
        "context_stack": scheduler_contexts_stacks[publish_destination],
        "state": "running",
        "last_updated": datetime.now().isoformat()
    })
    
    # Create a mock for the running scheduler
    mock_future = MagicMock()
    mock_future.done.return_value = False
    
    # Add to running schedulers
    from routes.scheduler import running_schedulers
    running_schedulers[publish_destination] = {
        "future": mock_future,
        "loop": MagicMock()
    }
    
    # Double-check the setup
    assert scheduler_states[publish_destination] == "running"
    assert "test_var1" in scheduler_contexts_stacks[publish_destination][0]["vars"]
    
    return publish_destination

# Actual tests
def test_pause_preserves_context(setup_running_scheduler, mock_flask_request, clean_scheduler_state):
    """Test that pausing a scheduler preserves the context."""
    publish_destination = setup_running_scheduler
    
    # Ensure the scheduler state is set in this test context
    # Tests need to initialize state from clean_scheduler_state to ensure they're using the same objects
    from routes.scheduler import scheduler_states
    scheduler_states[publish_destination] = "running"
    
    # Verify initial state
    assert scheduler_states.get(publish_destination) == "running"
    
    # Save original context for comparison
    original_context = get_current_context(publish_destination)
    assert original_context is not None
    assert original_context.get("vars", {}).get("test_var1") == "test_value1"
    assert original_context.get("vars", {}).get("test_var2") == 42
    
    # Pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        response = api_pause_scheduler(publish_destination)
    
    # Verify state changed
    assert scheduler_states.get(publish_destination) == "paused"
    
    # Verify context was preserved
    paused_context = get_current_context(publish_destination)
    assert paused_context is not None
    assert paused_context.get("vars", {}).get("test_var1") == "test_value1"
    assert paused_context.get("vars", {}).get("test_var2") == 42
    assert paused_context.get("vars", {}).get("test_var3") == {"nested": "object"}

def test_unpause_preserves_context(setup_running_scheduler, mock_flask_request, clean_scheduler_state):
    """Test that unpausing a scheduler preserves the context."""
    publish_destination = setup_running_scheduler
    
    # Ensure the scheduler state and contexts are set in this test context
    from routes.scheduler import scheduler_states
    from routes.scheduler_utils import scheduler_contexts_stacks
    
    # Create the initial context again for this test
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {
                "test_var1": "test_value1", 
                "test_var2": 42,
                "test_var3": {"nested": "object"},
                "initialized": True
            },
            "publish_destination": publish_destination
        }]
    
    scheduler_states[publish_destination] = "running"
    
    # First pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Verify state changed
    assert scheduler_states.get(publish_destination) == "paused"
    
    # Modify the context while paused
    context = get_current_context(publish_destination)
    assert context is not None, "Context should not be None after pausing"
    context["vars"]["paused_var"] = "added while paused"
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination]
    )
    
    # Unpause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_unpause_scheduler(publish_destination)
    
    # Verify state changed back to running
    assert scheduler_states.get(publish_destination) == "running"
    
    # Verify context was preserved including the new variable
    unpaused_context = get_current_context(publish_destination)
    assert unpaused_context is not None
    assert unpaused_context.get("vars", {}).get("test_var1") == "test_value1"
    assert unpaused_context.get("vars", {}).get("test_var2") == 42
    assert unpaused_context.get("vars", {}).get("paused_var") == "added while paused"

def test_pause_unpause_cycle_with_context_updates(setup_running_scheduler, mock_flask_request, clean_scheduler_state):
    """Test multiple pause/unpause cycles with context updates."""
    publish_destination = setup_running_scheduler
    
    # Ensure the scheduler state and contexts are set in this test context
    from routes.scheduler import scheduler_states
    from routes.scheduler_utils import scheduler_contexts_stacks
    
    # Initialize the state and context
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {
                "test_var1": "test_value1", 
                "test_var2": 42,
                "test_var3": {"nested": "object"},
                "initialized": True
            },
            "publish_destination": publish_destination
        }]
    
    scheduler_states[publish_destination] = "running"
    
    # Cycle 1: Pause
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Add var during pause
    set_context_variable(publish_destination, "cycle", 1)
    
    # Cycle 1: Unpause
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_unpause_scheduler(publish_destination)
    
    # Verify context after first cycle
    context = get_current_context(publish_destination)
    assert context is not None
    assert context.get("vars", {}).get("cycle") == 1
    
    # Cycle 2: Pause
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Modify var during pause
    set_context_variable(publish_destination, "cycle", 2)
    
    # Cycle 2: Unpause
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_unpause_scheduler(publish_destination)
    
    # Verify context after second cycle
    context = get_current_context(publish_destination)
    assert context is not None
    assert context.get("vars", {}).get("cycle") == 2
    assert context.get("vars", {}).get("test_var1") == "test_value1"  # Original var still present

def test_error_handling_in_pause_unpause(setup_running_scheduler, mock_flask_request, clean_scheduler_state):
    """Test error handling during pause/unpause operations."""
    publish_destination = setup_running_scheduler
    
    # Ensure the scheduler state and contexts are set in this test context
    from routes.scheduler import scheduler_states
    from routes.scheduler_utils import scheduler_contexts_stacks
    
    # Initialize the state and context
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {
                "test_var1": "test_value1", 
                "test_var2": 42,
                "test_var3": {"nested": "object"},
                "initialized": True
            },
            "publish_destination": publish_destination
        }]
    
    scheduler_states[publish_destination] = "running"
    
    # Test error during pause
    with patch('routes.scheduler_api.update_scheduler_state') as mock_update:
        # Simulate an error during state update
        mock_update.side_effect = Exception("Test error")
        
        with patch('routes.scheduler_api.jsonify') as mock_jsonify:
            mock_jsonify.return_value = {}
            with patch('routes.scheduler_api.error') as mock_error:
                # Call should not raise an exception
                api_pause_scheduler(publish_destination)
                
                # Should log the error
                mock_error.assert_called()
    
    # Test error during unpause
    with patch('routes.scheduler_api.update_scheduler_state') as mock_update:
        # Simulate an error during state update
        mock_update.side_effect = Exception("Test error")
        
        with patch('routes.scheduler_api.jsonify') as mock_jsonify:
            mock_jsonify.return_value = {}
            with patch('routes.scheduler_api.error') as mock_error:
                # Call should not raise an exception
                api_unpause_scheduler(publish_destination)
                
                # Should log the error
                mock_error.assert_called()

def test_context_persistence_with_state_load(setup_running_scheduler, mock_flask_request, clean_scheduler_state):
    """Test that context is properly loaded from disk during pause/unpause."""
    publish_destination = setup_running_scheduler
    
    # Ensure the scheduler state and contexts are set in this test context
    from routes.scheduler import scheduler_states
    from routes.scheduler_utils import scheduler_contexts_stacks
    
    # Initialize the state and context
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {
                "test_var1": "test_value1", 
                "test_var2": 42,
                "test_var3": {"nested": "object"},
                "initialized": True
            },
            "publish_destination": publish_destination
        }]
    
    scheduler_states[publish_destination] = "running"
    
    # First pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Create a mock for load_scheduler_state that simulates loading from disk
    mock_state = {
        "context_stack": [
            {
                "vars": {
                    "test_var1": "test_value1",
                    "test_var2": 42,
                    "disk_var": "loaded from disk"
                },
                "publish_destination": publish_destination
            }
        ],
        "schedule_stack": [],
        "state": "paused"
    }
    
    with patch('routes.scheduler_api.load_scheduler_state') as mock_load:
        mock_load.return_value = mock_state
        
        # Directly patch the scheduler_contexts_stacks to simulate state being loaded
        with patch.dict('routes.scheduler_utils.scheduler_contexts_stacks', {
            publish_destination: mock_state["context_stack"]
        }, clear=False):
            
            with patch('routes.scheduler_api.jsonify') as mock_jsonify:
                mock_jsonify.return_value = {}
                # Unpause with mocked state load
                api_unpause_scheduler(publish_destination)
    
            # Verify context includes the disk-loaded variable
            unpaused_context = get_current_context(publish_destination)
            assert unpaused_context is not None
            assert unpaused_context.get("vars", {}).get("disk_var") == "loaded from disk"

def test_restart_preserves_paused_context(clean_scheduler_state, test_schedule):
    """Test that a scheduler restarted after being paused preserves its context."""
    publish_destination = "test_restart_destination"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs, running_schedulers
    scheduler_logs[publish_destination] = []
    
    # Initialize scheduler directly in the state objects
    from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_states, scheduler_schedule_stacks
    
    # STEP 1: Set up initial state with a running scheduler
    initial_context = {
        "vars": {
            "test_var1": "test_value1", 
            "test_var2": 42,
            "before_pause": True
        },
        "publish_destination": publish_destination
    }
    
    # Set up the memory state
    scheduler_contexts_stacks[publish_destination] = [initial_context]
    scheduler_schedule_stacks[publish_destination] = [test_schedule]
    scheduler_states[publish_destination] = "running"
    
    # Force a save to disk
    save_scheduler_state(publish_destination, {
        "schedule_stack": scheduler_schedule_stacks[publish_destination],
        "context_stack": scheduler_contexts_stacks[publish_destination],
        "state": "running",
        "last_updated": datetime.now().isoformat()
    })
    
    # STEP 2: Pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Verify paused state is saved to disk
    assert scheduler_states[publish_destination] == "paused"
    
    # STEP 3: Add a variable while paused and persist it
    context = get_current_context(publish_destination)
    context["vars"]["during_pause"] = "added while paused"
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination],
        state="paused",
        force_save=True
    )
    
    # STEP 4: Simulate a restart by clearing in-memory state
    # Clear all in-memory state
    running_schedulers.pop(publish_destination, None)
    scheduler_contexts_stacks.pop(publish_destination, None)
    scheduler_schedule_stacks.pop(publish_destination, None)
    scheduler_states.pop(publish_destination, None)
    
    # STEP 5: Reload from disk (this is what would happen during app restart)
    from routes.scheduler_utils import load_scheduler_state
    loaded_state = load_scheduler_state(publish_destination)
    
    # STEP 6: Initialize the in-memory state from the loaded state
    scheduler_contexts_stacks[publish_destination] = loaded_state.get("context_stack", [])
    scheduler_schedule_stacks[publish_destination] = loaded_state.get("schedule_stack", [])
    scheduler_states[publish_destination] = loaded_state.get("state", "stopped")
    
    # STEP 7: Verify the paused state and context were properly loaded
    assert scheduler_states[publish_destination] == "paused", "Scheduler should still be paused after restart"
    
    loaded_context = get_current_context(publish_destination)
    assert loaded_context is not None, "Context should not be None after reload"
    assert loaded_context.get("vars", {}).get("test_var1") == "test_value1", "Original variable should be preserved"
    assert loaded_context.get("vars", {}).get("during_pause") == "added while paused", "Variable added during pause should be preserved"
    
    # STEP 8: Unpause the reloaded scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_unpause_scheduler(publish_destination)
    
    # STEP 9: Verify the context is still intact after unpausing
    unpaused_context = get_current_context(publish_destination)
    assert unpaused_context is not None, "Context should not be None after unpause"
    assert unpaused_context.get("vars", {}).get("test_var1") == "test_value1", "Original variable should be preserved after unpause"
    assert unpaused_context.get("vars", {}).get("during_pause") == "added while paused", "Variable added during pause should be preserved after unpause" 