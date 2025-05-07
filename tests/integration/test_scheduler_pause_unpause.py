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
    set_context_variable
)
from routes.scheduler_api import (
    api_pause_scheduler, api_unpause_scheduler
)

# Fixtures
@pytest.fixture
def clean_scheduler_state():
    """Clean up scheduler state before and after tests."""
    # Store original state
    original_states = dict(scheduler_states)
    original_contexts = {k: v[:] for k, v in scheduler_contexts_stacks.items()}
    
    # Clear state for test
    scheduler_states.clear()
    scheduler_contexts_stacks.clear()
    
    yield
    
    # Restore original state
    scheduler_states.clear()
    scheduler_states.update(original_states)
    
    scheduler_contexts_stacks.clear()
    scheduler_contexts_stacks.update(original_contexts)

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
            "instructions": [
                {"action": "set_var", "var_name": "initialized", "value": True}
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
                            "instructions": [
                                {"action": "set_var", "var_name": "lunch_time", "value": True}
                            ]
                        }
                    }
                ]
            }
        ]
    }

@pytest.fixture
def setup_running_scheduler(clean_scheduler_state, test_schedule):
    """Set up a running scheduler with context for testing."""
    publish_destination = "test_destination"
    
    # Initialize scheduler
    start_scheduler(publish_destination, test_schedule)
    
    # Set some variables in the context
    context = scheduler_contexts_stacks.get(publish_destination, [{}])[0]
    if "vars" not in context:
        context["vars"] = {}
    
    context["vars"]["test_var1"] = "test_value1"
    context["vars"]["test_var2"] = 42
    context["vars"]["test_var3"] = {"nested": "object"}
    
    # Update state to persist variables
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination]
    )
    
    return publish_destination

# Actual tests
def test_pause_preserves_context(setup_running_scheduler, mock_flask_request):
    """Test that pausing a scheduler preserves the context."""
    publish_destination = setup_running_scheduler
    
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

def test_unpause_preserves_context(setup_running_scheduler, mock_flask_request):
    """Test that unpausing a scheduler preserves the context."""
    publish_destination = setup_running_scheduler
    
    # First pause the scheduler
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_jsonify.return_value = {}
        api_pause_scheduler(publish_destination)
    
    # Verify state changed
    assert scheduler_states.get(publish_destination) == "paused"
    
    # Modify the context while paused
    context = get_current_context(publish_destination)
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

def test_pause_unpause_cycle_with_context_updates(setup_running_scheduler, mock_flask_request):
    """Test multiple pause/unpause cycles with context updates."""
    publish_destination = setup_running_scheduler
    
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
    assert context.get("vars", {}).get("cycle") == 2
    assert context.get("vars", {}).get("test_var1") == "test_value1"  # Original var still present

def test_error_handling_in_pause_unpause(setup_running_scheduler, mock_flask_request):
    """Test error handling during pause/unpause operations."""
    publish_destination = setup_running_scheduler
    
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

def test_context_persistence_with_state_load(setup_running_scheduler, mock_flask_request):
    """Test that context is properly loaded from disk during pause/unpause."""
    publish_destination = setup_running_scheduler
    
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
        
        with patch('routes.scheduler_api.jsonify') as mock_jsonify:
            mock_jsonify.return_value = {}
            # Unpause with mocked state load
            api_unpause_scheduler(publish_destination)
    
    # Verify context includes the disk-loaded variable
    unpaused_context = get_current_context(publish_destination)
    assert unpaused_context is not None
    assert unpaused_context.get("vars", {}).get("disk_var") == "loaded from disk" 