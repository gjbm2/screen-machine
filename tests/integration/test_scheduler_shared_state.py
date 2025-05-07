import pytest
import asyncio
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

from routes.scheduler import (
    start_scheduler, stop_scheduler, running_schedulers,
    get_event_loop, stop_event_loop
)
from routes.scheduler_utils import (
    scheduler_states, scheduler_schedule_stacks, scheduler_contexts_stacks,
    update_scheduler_state
)
from routes.scheduler_api import (
    api_pause_scheduler, api_unpause_scheduler, api_load_schedule
)

@pytest.fixture
def mock_flask_request():
    """Mock Flask request context for API calls."""
    class MockRequest:
        def __init__(self, json_data=None):
            self._json = json_data or {}
        
        def get_json(self):
            return self._json
    
    with patch('routes.scheduler_api.request') as mock_request:
        mock_request.json = {}
        mock_request.get_json = lambda: mock_request.json
        yield mock_request

@pytest.fixture
def mock_event_loop():
    """Mock asyncio event loop for testing event loop management."""
    mock_loop = MagicMock()
    
    # Track if loop is stopped
    mock_loop.is_stopped = False
    
    def mock_stop():
        mock_loop.is_stopped = True
    
    mock_loop.stop = mock_stop
    
    with patch('routes.scheduler._event_loop', mock_loop):
        yield mock_loop

@pytest.fixture
def multi_destination_setup(clean_scheduler_state):
    """Set up multiple destinations for testing."""
    destinations = ["test_dest1", "test_dest2", "test_dest3"]
    
    # Basic schedule template
    basic_schedule = {
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                "scheduled_actions": [
                    {
                        "time": "08:00",
                        "repeat_schedule": {
                            "every": "60",
                            "until": "23:00"
                        },
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "test_var", "input": {"value": "test"}}
                            ]
                        }
                    }
                ]
            }
        ],
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "init_var", "input": {"value": "initialized"}}
            ]
        }
    }
    
    # Initialize each destination
    for dest in destinations:
        # Clear any existing state
        if dest in scheduler_states:
            scheduler_states[dest] = "stopped"
        
        if dest in scheduler_schedule_stacks:
            scheduler_schedule_stacks[dest] = []
        
        if dest in scheduler_contexts_stacks:
            scheduler_contexts_stacks[dest] = []
            
        # Create a context
        scheduler_contexts_stacks[dest] = [{
            "vars": {},
            "publish_destination": dest
        }]
        
        # Add schedule to stack
        scheduler_schedule_stacks[dest] = [basic_schedule]
        
        # Update state
        update_scheduler_state(
            dest,
            schedule_stack=scheduler_schedule_stacks[dest],
            context_stack=scheduler_contexts_stacks[dest],
            state="stopped"
        )
    
    return {
        "destinations": destinations,
        "basic_schedule": basic_schedule
    }

@pytest.mark.asyncio
async def test_pause_unpause_doesnt_stop_event_loop(multi_destination_setup, mock_event_loop):
    """Test that pausing a scheduler doesn't stop the event loop."""
    destinations = multi_destination_setup["destinations"]
    
    # Mock run_coroutine_threadsafe to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
        # Start first scheduler
        start_scheduler(destinations[0], scheduler_schedule_stacks[destinations[0]][-1])
        
        # Start second scheduler
        start_scheduler(destinations[1], scheduler_schedule_stacks[destinations[1]][-1])
        
        # Verify both are running
        assert destinations[0] in running_schedulers
        assert destinations[1] in running_schedulers
        assert scheduler_states[destinations[0]] == "running"
        assert scheduler_states[destinations[1]] == "running"
        
        # Pause the first scheduler
        scheduler_states[destinations[0]] = "paused"
        update_scheduler_state(destinations[0], state="paused")
        
        # Verify it's paused but the event loop is still running
        assert scheduler_states[destinations[0]] == "paused"
        assert not mock_event_loop.is_stopped
        
        # Stop the second scheduler
        stop_scheduler(destinations[1])
        
        # Verify the event loop is still running because we have a paused scheduler
        assert not mock_event_loop.is_stopped
        
        # Unpause first scheduler
        scheduler_states[destinations[0]] = "running"
        update_scheduler_state(destinations[0], state="running")
        
        # Now stop the first scheduler too
        stop_scheduler(destinations[0])
        
        # Verify the event loop is stopped since all schedulers are stopped
        assert mock_event_loop.is_stopped

@pytest.mark.asyncio
async def test_loading_schedule_preserves_other_destinations(multi_destination_setup, mock_flask_request):
    """Test that loading a schedule for one destination doesn't affect others."""
    destinations = multi_destination_setup["destinations"]
    
    # Mock run_coroutine_threadsafe to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
        # Start first and second schedulers
        start_scheduler(destinations[0], scheduler_schedule_stacks[destinations[0]][-1])
        start_scheduler(destinations[1], scheduler_schedule_stacks[destinations[1]][-1])
        
        # Pause the second scheduler
        scheduler_states[destinations[1]] = "paused"
        update_scheduler_state(destinations[1], state="paused")
        
        # Verify initial states
        assert scheduler_states[destinations[0]] == "running"
        assert scheduler_states[destinations[1]] == "paused"
        
        # Create a new schedule to load
        new_schedule = {
            "triggers": [
                {
                    "type": "day_of_week",
                    "days": ["Monday"],
                    "scheduled_actions": [
                        {
                            "time": "09:00",
                            "trigger_actions": {
                                "instructions_block": [
                                    {"action": "set_var", "var": "new_var", "input": {"value": "new_value"}}
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        
        # Set up mock request with the new schedule
        mock_flask_request.json = new_schedule
        
        # Mock jsonschema validation to always succeed
        with patch('jsonschema.validate', return_value=None):
            # Load new schedule for the third destination
            with patch('routes.scheduler_api.request.get_json', return_value=new_schedule):
                # When loading a new schedule
                api_load_schedule(destinations[2])
                
                # Verify third destination got the new schedule
                assert len(scheduler_schedule_stacks[destinations[2]]) == 1
                assert scheduler_schedule_stacks[destinations[2]][-1] == new_schedule
                
                # Verify first destination is still running
                assert scheduler_states[destinations[0]] == "running"
                
                # Verify second destination is still paused
                assert scheduler_states[destinations[1]] == "paused"

@pytest.mark.asyncio
async def test_multiple_destinations_with_different_states(multi_destination_setup, mock_event_loop):
    """Test managing multiple destinations with different states."""
    destinations = multi_destination_setup["destinations"]
    
    # Mock run_coroutine_threadsafe to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
        # Start all three schedulers
        for dest in destinations:
            start_scheduler(dest, scheduler_schedule_stacks[dest][-1])
            assert dest in running_schedulers
            assert scheduler_states[dest] == "running"
        
        # Pause the first scheduler
        scheduler_states[destinations[0]] = "paused"
        update_scheduler_state(destinations[0], state="paused")
        
        # Stop the second scheduler
        stop_scheduler(destinations[1])
        
        # Verify states
        assert scheduler_states[destinations[0]] == "paused"
        assert scheduler_states[destinations[1]] == "stopped"
        assert scheduler_states[destinations[2]] == "running"
        
        # The event loop should still be running
        assert not mock_event_loop.is_stopped
        
        # Stop the third scheduler
        stop_scheduler(destinations[2])
        
        # Event loop should still be running because of the paused scheduler
        assert not mock_event_loop.is_stopped
        
        # Unpause and stop the first scheduler
        scheduler_states[destinations[0]] = "running"
        update_scheduler_state(destinations[0], state="running")
        stop_scheduler(destinations[0])
        
        # Now the event loop should be stopped
        assert mock_event_loop.is_stopped

@pytest.mark.asyncio
async def test_api_pause_unpause(multi_destination_setup, mock_event_loop):
    """Test the API pause/unpause endpoints maintain proper state."""
    destinations = multi_destination_setup["destinations"]
    
    # Mock run_coroutine_threadsafe to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
        # Start the first scheduler
        start_scheduler(destinations[0], scheduler_schedule_stacks[destinations[0]][-1])
        
        # Use the API to pause it
        response = api_pause_scheduler(destinations[0])
        
        # Verify response and state
        assert response.json["status"] == "paused"
        assert scheduler_states[destinations[0]] == "paused"
        
        # Use the API to unpause it
        response = api_unpause_scheduler(destinations[0])
        
        # Verify response and state
        assert response.json["status"] == "running"
        assert scheduler_states[destinations[0]] == "running"

@pytest.mark.asyncio
async def test_schedule_loading_with_paused_scheduler(multi_destination_setup, mock_flask_request):
    """Test loading a schedule when another scheduler is paused."""
    destinations = multi_destination_setup["destinations"]
    
    # Mock run_coroutine_threadsafe to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
        # Start and pause the first scheduler
        start_scheduler(destinations[0], scheduler_schedule_stacks[destinations[0]][-1])
        scheduler_states[destinations[0]] = "paused"
        update_scheduler_state(destinations[0], state="paused")
        
        # Verify it's paused
        assert scheduler_states[destinations[0]] == "paused"
        
        # Create a new schedule
        new_schedule = {
            "triggers": [
                {
                    "type": "day_of_week",
                    "days": ["Monday"],
                    "scheduled_actions": []
                }
            ]
        }
        
        # Set up request mock
        mock_flask_request.json = new_schedule
        
        # Mock jsonschema validation
        with patch('jsonschema.validate', return_value=None):
            # Load a schedule for the second destination
            with patch('routes.scheduler_api.request.get_json', return_value=new_schedule):
                api_load_schedule(destinations[1])
                
                # Verify the first scheduler is still paused
                assert scheduler_states[destinations[0]] == "paused"
                
                # Verify the second destination has the new schedule
                assert len(scheduler_schedule_stacks[destinations[1]]) == 1
                assert scheduler_schedule_stacks[destinations[1]][-1] == new_schedule 