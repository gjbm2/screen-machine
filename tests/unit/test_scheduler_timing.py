import pytest
from datetime import datetime, timedelta
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

# Import the functions we want to test
from routes.scheduler_utils import (
    process_time_schedules, 
    last_trigger_executions,
    save_scheduler_state,
    load_scheduler_state
)

# Test fixtures
@pytest.fixture
def basic_schedule():
    return {
        "time": "08:00",
        "repeat_schedule": {
            "every": "1",
            "until": "23:00"
        },
        "trigger_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "test", "input": {"value": "test"}}
            ]
        }
    }

@pytest.fixture
def fractional_schedule():
    return {
        "time": "08:00",
        "repeat_schedule": {
            "every": "0.5",  # 30 seconds
            "until": "23:00"
        },
        "trigger_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "test", "input": {"value": "test"}}
            ]
        }
    }

# Clear the global execution registry between tests
@pytest.fixture(autouse=True)
def clear_executions():
    # Clear before test
    if "test_dest" in last_trigger_executions:
        last_trigger_executions["test_dest"].clear()
    yield
    # Clear after test
    if "test_dest" in last_trigger_executions:
        last_trigger_executions["test_dest"].clear()

def test_basic_schedule_execution(basic_schedule):
    """Test that a schedule with 1-minute interval executes exactly once per minute."""
    # Set up a simulated initial time
    base_time = datetime(2025, 1, 1, 8, 15, 0)  # 8:15 AM
    minute_of_day = base_time.hour * 60 + base_time.minute
    
    # First execution at 8:15:00
    matched = process_time_schedules([basic_schedule], base_time, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should match and execute at first tick"
    
    # Check at 8:15:10 - should not execute again
    matched = process_time_schedules([basic_schedule], base_time + timedelta(seconds=10), minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again in the same minute"
    
    # Check at 8:15:50 - should not execute again
    matched = process_time_schedules([basic_schedule], base_time + timedelta(seconds=50), minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again in the same minute"
    
    # Next minute (8:16:00) - should execute again
    next_minute = base_time + timedelta(minutes=1)
    minute_of_day = next_minute.hour * 60 + next_minute.minute
    matched = process_time_schedules([basic_schedule], next_minute, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should execute again at next minute"

def test_fractional_schedule_execution(fractional_schedule):
    """Test that a schedule with 0.5-minute (30-second) interval executes at correct intervals."""
    # Set up a simulated initial time
    base_time = datetime(2025, 1, 1, 8, 15, 0)  # 8:15:00
    minute_of_day = base_time.hour * 60 + base_time.minute
    
    # First execution at 8:15:00
    matched = process_time_schedules([fractional_schedule], base_time, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should match and execute at first tick"
    
    # Check at 8:15:10 - should not execute again
    matched = process_time_schedules([fractional_schedule], base_time + timedelta(seconds=10), minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again before interval completion"
    
    # Check at 8:15:20 - should not execute again
    matched = process_time_schedules([fractional_schedule], base_time + timedelta(seconds=20), minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again before interval completion"
    
    # Check at 8:15:30 - should execute again (30 seconds = 0.5 minutes)
    half_minute = base_time + timedelta(seconds=30)
    matched = process_time_schedules([fractional_schedule], half_minute, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should execute again after 30 seconds (0.5 minutes)"
    
    # Check at 8:15:40 - should not execute again
    matched = process_time_schedules([fractional_schedule], base_time + timedelta(seconds=40), minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again before interval completion"

def test_persistence_of_executions():
    """Test that execution history is properly saved and loaded."""
    # Set up a test schedule
    schedule = {
        "time": "08:00",
        "repeat_schedule": {
            "every": "1",
            "until": "23:00"
        }
    }
    
    # Set up a simulated time
    test_time = datetime(2025, 1, 1, 8, 15, 0)
    minute_of_day = test_time.hour * 60 + test_time.minute
    
    # Create a temp file for storing state
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmpfile:
        temp_path = tmpfile.name
    
    try:
        # Mock the storage path function to use our temp file
        with patch('routes.scheduler_utils.get_scheduler_storage_path', return_value=temp_path):
            # Execute the schedule once
            process_time_schedules([schedule], test_time, minute_of_day, "test_dest")
            
            # Verify it was executed
            assert "test_dest" in last_trigger_executions
            assert len(last_trigger_executions["test_dest"]) > 0
            
            # Save the state
            state = {"state": "running"}
            save_scheduler_state("test_dest", state)
            
            # Clear the in-memory state
            last_trigger_executions["test_dest"].clear()
            assert len(last_trigger_executions["test_dest"]) == 0
            
            # Load the state back
            load_scheduler_state("test_dest")
            
            # Verify the execution history was restored
            assert "test_dest" in last_trigger_executions
            assert len(last_trigger_executions["test_dest"]) > 0
            
            # Try to execute again at the same time - should not execute
            matched = process_time_schedules([schedule], test_time, minute_of_day, "test_dest")
            assert len(matched) == 0, "Schedule should not execute again in the same interval after reload"
    finally:
        # Clean up the temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)

def test_system_restart_behavior():
    """Test that the system doesn't execute multiple times after being down."""
    # Set up a test schedule with 1-minute interval
    schedule = {
        "time": "08:00",
        "repeat_schedule": {
            "every": "1",
            "until": "23:00"
        }
    }
    
    # Set up initial time
    start_time = datetime(2025, 1, 1, 8, 0, 0)
    minute_of_day = start_time.hour * 60 + start_time.minute
    
    # First execution
    matched = process_time_schedules([schedule], start_time, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should execute at start"
    
    # Simulate system down for 5 minutes
    # New time is 8:05:00
    new_time = start_time + timedelta(minutes=5)
    minute_of_day = new_time.hour * 60 + new_time.minute
    
    # Should execute once at new time, not 5 times for missed minutes
    matched = process_time_schedules([schedule], new_time, minute_of_day, "test_dest")
    assert len(matched) == 1, "Schedule should execute once after restart, not for each missed interval"
    
    # Immediate second call should not execute again
    matched = process_time_schedules([schedule], new_time, minute_of_day, "test_dest")
    assert len(matched) == 0, "Schedule should not execute again in the same minute"

def test_stable_ids_across_runs():
    """Test that schedule IDs remain stable for identical schedules."""
    # Create two identical schedules
    schedule1 = {
        "time": "08:00",
        "repeat_schedule": {
            "every": "1",
            "until": "23:00"
        }
    }
    
    schedule2 = {
        "time": "08:00",
        "repeat_schedule": {
            "every": "1",
            "until": "23:00"
        }
    }
    
    # Set up a simulated time
    test_time = datetime(2025, 1, 1, 8, 15, 0)
    minute_of_day = test_time.hour * 60 + test_time.minute
    
    # Execute the first schedule
    process_time_schedules([schedule1], test_time, minute_of_day, "test_dest")
    
    # Try to execute the second schedule (identical content)
    matched = process_time_schedules([schedule2], test_time, minute_of_day, "test_dest")
    
    # Should not execute because the ID should be the same
    assert len(matched) == 0, "Identical schedules should have the same ID and not execute twice" 