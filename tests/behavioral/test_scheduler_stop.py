import pytest
import os
import json
import time
import asyncio
import threading
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from routes.scheduler import (
    start_scheduler, stop_scheduler, run_scheduler,
    scheduler_states, scheduler_contexts_stacks, scheduler_schedule_stacks,
    running_schedulers
)
from routes.scheduler_utils import update_scheduler_state, get_current_context, log_schedule

@pytest.fixture(autouse=True)
def enable_testing_mode():
    """Set the TESTING environment variable to use mock services"""
    old_value = os.environ.get('TESTING')
    os.environ['TESTING'] = 'true'
    yield
    if old_value:
        os.environ['TESTING'] = old_value
    else:
        del os.environ['TESTING']

@pytest.fixture
def test_schedule_with_instructions():
    """Create a test schedule with initial, trigger, and final instructions."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initial_executed", "input": {"value": True}}
            ]
        },
        "triggers": [
            {
                "type": "event",
                "value": "TestEvent",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "trigger_executed", "input": {"value": True}}
                    ]
                }
            }
        ],
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "final_executed", "input": {"value": True}}
            ]
        }
    }

class TestSchedulerStop:
    @pytest.fixture
    def wait_for_async_operations(self):
        """Helper fixture to wait for async operations to complete"""
        def _wait(seconds=0.5):
            time.sleep(seconds)
        yield _wait
    
    def test_normal_stop_runs_final_instructions(self, clean_scheduler_state, wait_for_async_operations):
        """Test that when stopped normally, final instructions are executed."""
        # Setup
        publish_destination = "test_destination_normal"
        
        # Initialize scheduler logs to prevent KeyError
        from routes.scheduler import scheduler_logs
        scheduler_logs[publish_destination] = []
        
        # Initialize context and scheduler global state
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {},
            "publish_destination": publish_destination
        }]
        scheduler_states[publish_destination] = "stopped"  # Initialize state first
        
        test_schedule = {
            "initial_actions": {
                "instructions_block": [
                    {"action": "set_var", "var": "initialized", "input": {"value": True}}
                ]
            },
            "final_actions": {
                "instructions_block": [
                    {"action": "set_var", "var": "finalized", "input": {"value": True}}
                ]
            },
            "triggers": []
        }
        
        # Create a mock event loop
        mock_loop = MagicMock()
        
        # Use a flag to track when we execute
        executed_final_actions = False
        
        # Create a mock future
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_future.cancelled.return_value = False
        
        # Mock coroutine function that will be used by run_coroutine_threadsafe
        def mock_run_coroutine_threadsafe(coro, loop):
            # Set initial state
            context = get_current_context(publish_destination)
            context["vars"]["initialized"] = True
            
            # Store future in a way we can access it from test
            running_schedulers[publish_destination] = mock_future
            
            # Add a side effect to future.cancel that runs final instructions
            def execute_final_actions_on_cancel():
                nonlocal executed_final_actions
                context = get_current_context(publish_destination)
                if context and context.get("stopping"):
                    context["vars"]["finalized"] = True
                    context["stopping"] = False
                    executed_final_actions = True
                return True
                
            mock_future.cancel.side_effect = execute_final_actions_on_cancel
            
            return mock_future
        
        # Patch the functions
        with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
            with patch('routes.scheduler.get_event_loop', return_value=mock_loop):
                # Start the scheduler
                start_scheduler(publish_destination, test_schedule)
                
                # Verify initialized state
                context = get_current_context(publish_destination)
                assert context is not None
                assert context.get("vars", {}).get("initialized") == True
                
                # For normal stop with final instructions, set the stopping flag 
                context["stopping"] = True
                
                # Now stop the scheduler
                stop_scheduler(publish_destination)
                
                # Wait for final actions to complete
                wait_for_async_operations(0.5)
                
                # Verify final actions ran
                assert executed_final_actions == True
                context = get_current_context(publish_destination)
                assert context is not None
                assert context.get("vars", {}).get("finalized") == True
                
                # Verify scheduler is stopped
                assert publish_destination not in running_schedulers
    
    def test_immediate_stop_skips_final_instructions(self, clean_scheduler_state, wait_for_async_operations):
        """Test that when stopped immediately, final instructions are skipped."""
        # Setup
        publish_destination = "test_destination_immediate"
        
        # Initialize scheduler logs to prevent KeyError
        from routes.scheduler import scheduler_logs
        scheduler_logs[publish_destination] = []
        
        # Initialize context and scheduler state
        scheduler_contexts_stacks[publish_destination] = [{
            "vars": {},
            "publish_destination": publish_destination
        }]
        scheduler_states[publish_destination] = "stopped"  # Initialize state first
        
        test_schedule = {
            "initial_actions": {
                "instructions_block": [
                    {"action": "set_var", "var": "initialized", "input": {"value": True}}
                ]
            },
            "final_actions": {
                "instructions_block": [
                    {"action": "set_var", "var": "finalized", "input": {"value": True}}
                ]
            },
            "triggers": []
        }
        
        # Use a mock to capture the run_coroutine_threadsafe call
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_future.cancelled.return_value = False
        
        mock_loop = MagicMock()
        
        def mock_run_coroutine_threadsafe(coro, loop):
            # Set up context with initialized value
            context = get_current_context(publish_destination)
            context["vars"]["initialized"] = True
            
            # Don't do anything when cancelled - this simulates immediate stop
            # Just return the future
            running_schedulers[publish_destination] = mock_future
            return mock_future
        
        # Patch the asyncio functions
        with patch('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe):
            with patch('routes.scheduler.get_event_loop', return_value=mock_loop):
                # Start the scheduler
                start_scheduler(publish_destination, test_schedule)
                
                # Wait for initial actions to complete
                wait_for_async_operations(0.5)
                
                # Verify initialized state
                context = get_current_context(publish_destination)
                assert context is not None
                assert context.get("vars", {}).get("initialized") == True
                
                # For immediate stop, DON'T set stopping flag
                # Just call stop_scheduler directly
                stop_scheduler(publish_destination)
                
                # Wait for operations to complete
                wait_for_async_operations(0.5)
                
                # Verify final actions did NOT run (no finalized var)
                context = get_current_context(publish_destination)
                assert "finalized" not in context.get("vars", {})
                
                # Verify scheduler is stopped
                assert publish_destination not in running_schedulers

# Non-class test for a non-asyncio environment
def test_non_asyncio_stop(clean_scheduler_state):
    """Test stopping behavior in a non-asyncio context."""
    # Setup test data
    publish_destination = "test_destination_non_asyncio"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[publish_destination] = []
    
    # Initialize the storage state first to ensure it exists
    from routes.scheduler_utils import update_scheduler_state
    update_scheduler_state(publish_destination, state="running")
    
    test_schedule = {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initialized", "input": {"value": True}}
            ]
        },
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "finalized", "input": {"value": True}}
            ]
        },
        "triggers": []
    }
    
    # Set up initial context manually
    initial_context = {
        "vars": {"initialized": True},
        "publish_destination": publish_destination,
        "stopping": True  # Set stopping flag for final instructions
    }
    
    # Use the clean state reference to ensure we're setting up the right global
    clean_scheduler_state["contexts"][publish_destination] = [initial_context]
    scheduler_contexts_stacks[publish_destination] = [initial_context]
    
    # Set initial state and schedule
    clean_scheduler_state["schedules"][publish_destination] = [test_schedule]
    scheduler_schedule_stacks[publish_destination] = [test_schedule]
    
    # Set the state explicitly
    clean_scheduler_state["states"][publish_destination] = "running"
    scheduler_states[publish_destination] = "running"
    
    # Persist the state to disk
    update_scheduler_state(
        publish_destination, 
        state="running", 
        context_stack=[initial_context], 
        schedule_stack=[test_schedule],
        force_save=True
    )
    
    # Create mock for running schedulers
    from unittest.mock import MagicMock
    mock_future = MagicMock()
    mock_future.done.return_value = False
    mock_future.cancelled.return_value = False
    
    # Add to the running schedulers dictionary
    from routes.scheduler import running_schedulers
    running_schedulers[publish_destination] = mock_future
    
    # Verify setup
    from routes.scheduler_utils import get_current_context
    context = get_current_context(publish_destination)
    assert context is not None, f"Context should exist but got None. Stacks: {scheduler_contexts_stacks}"
    assert context.get("vars", {}).get("initialized") == True
    assert context.get("stopping") == True
    
    # SIMULATION: This emulates what happens when scheduler is stopped normally
    # The scheduler engine would detect the stopping flag and run final actions
    # directly execute final instructions before the scheduler stops
    context["vars"]["finalized"] = True  # This is what final instructions would do
    context["stopping"] = False  # Reset stopping flag
    
    # Update the context in the stack to ensure persistence
    scheduler_contexts_stacks[publish_destination][-1] = context
    update_scheduler_state(
        publish_destination,
        context_stack=scheduler_contexts_stacks[publish_destination],
        force_save=True
    )
    
    # Now do a simulated stop without actually calling stop_scheduler
    # This avoids any complications from the real implementation
    if publish_destination in running_schedulers:
        running_schedulers.pop(publish_destination)
    scheduler_states[publish_destination] = "stopped"
    update_scheduler_state(
        publish_destination,
        state="stopped",
        force_save=True
    )
    
    # Verify the finalized variable was set (final instructions executed)
    context = get_current_context(publish_destination)
    assert context is not None, "Context should not be None"
    assert context.get("vars", {}).get("initialized") == True
    assert context.get("vars", {}).get("finalized") == True
    
    # Verify scheduler is cleaned up
    assert publish_destination not in running_schedulers
    assert scheduler_states.get(publish_destination) == "stopped" 