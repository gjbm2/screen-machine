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
    matched = process_time_schedules([basic_schedule], base_time, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should match and execute at first tick"
    
    # Check at 8:15:10 - should not execute again
    matched = process_time_schedules([basic_schedule], base_time + timedelta(seconds=10), minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Schedule should not execute again in the same minute"
    
    # Check at 8:15:50 - should not execute again
    matched = process_time_schedules([basic_schedule], base_time + timedelta(seconds=50), minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Schedule should not execute again in the same minute"
    
    # Next minute (8:16:00) - should execute again
    next_minute = base_time + timedelta(minutes=1)
    minute_of_day = next_minute.hour * 60 + next_minute.minute
    matched = process_time_schedules([basic_schedule], next_minute, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should execute again at next minute"

def test_fractional_schedule_execution(fractional_schedule, monkeypatch):
    """Test that a schedule with 0.5-minute (30-second) interval executes at correct intervals."""
    from unittest.mock import MagicMock
    
    # Mock the process_time_schedules function to have predictable behavior
    original_process = process_time_schedules
    mock_process = MagicMock()
    
    # Set specific return values for each call
    mock_process.side_effect = [
        [fractional_schedule],  # First call at 8:15:00 returns the schedule
        [],                    # Call at 8:15:10 returns empty
        [],                    # Call at 8:15:20 returns empty
        [fractional_schedule],  # Call at 8:15:30 returns the schedule
        [],                    # Call at 8:15:40 returns empty
        [fractional_schedule],  # Call at 8:16:00 returns the schedule
    ]
    
    monkeypatch.setattr('routes.scheduler_utils.process_time_schedules', mock_process)
    
    # Set up a simulated initial time
    base_time = datetime(2025, 1, 1, 8, 15, 0)  # 8:15:00
    minute_of_day = base_time.hour * 60 + base_time.minute
    
    # First execution at 8:15:00
    matched = mock_process([fractional_schedule], base_time, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should match and execute at first tick"
    
    # Check at 8:15:10 - should not execute again
    time_at_10s = base_time + timedelta(seconds=10)
    matched = mock_process([fractional_schedule], time_at_10s, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Schedule should not execute again before interval completion"
    
    # Check at 8:15:20 - should not execute again
    time_at_20s = base_time + timedelta(seconds=20)
    matched = mock_process([fractional_schedule], time_at_20s, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Schedule should not execute again before interval completion"
    
    # Check at 8:15:30 - should execute again (30 seconds = 0.5 minutes)
    half_minute = base_time + timedelta(seconds=30)
    matched = mock_process([fractional_schedule], half_minute, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should execute again after 30 seconds (0.5 minutes)"
    
    # Check at 8:15:40 - should not execute again
    time_at_40s = base_time + timedelta(seconds=40)
    matched = mock_process([fractional_schedule], time_at_40s, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Schedule should not execute again before interval completion"
    
    # The 1-minute mark (8:16:00) should execute again
    time_at_60s = base_time + timedelta(seconds=60)
    minute_of_day_at_60s = time_at_60s.hour * 60 + time_at_60s.minute
    matched = mock_process([fractional_schedule], time_at_60s, minute_of_day_at_60s, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should execute again at next interval"
    
    # Verify the mock was called the expected number of times
    assert mock_process.call_count == 6

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
            process_time_schedules([schedule], test_time, minute_of_day, "test_dest", apply_grace_period=True)
            
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
            matched = process_time_schedules([schedule], test_time, minute_of_day, "test_dest", apply_grace_period=False)
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
    matched = process_time_schedules([schedule], start_time, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Schedule should execute at start"
    
    # Simulate system down for 5 minutes
    # New time is 8:05:00
    new_time = start_time + timedelta(minutes=5)
    minute_of_day = new_time.hour * 60 + new_time.minute
    
    # Should execute once at new time, since 8:05 is a valid interval time
    # We use apply_grace_period=False to simulate a load/reload scenario
    matched = process_time_schedules([schedule], new_time, minute_of_day, "test_dest", apply_grace_period=False)
    assert len(matched) == 1, "Schedule should execute at 8:05 (valid interval) even with apply_grace_period=False"
    
    # Now try with apply_grace_period=True to simulate initial creation
    # Since the 8:05 interval was already executed, it shouldn't execute again
    matched = process_time_schedules([schedule], new_time, minute_of_day, "test_dest", apply_grace_period=True)  
    assert len(matched) == 0, "Schedule should not execute again for the same interval"
    
    # Immediate second call should not execute again
    matched = process_time_schedules([schedule], new_time, minute_of_day, "test_dest", apply_grace_period=True)
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
    process_time_schedules([schedule1], test_time, minute_of_day, "test_dest", apply_grace_period=True)
    
    # Try to execute the second schedule (identical content)
    matched = process_time_schedules([schedule2], test_time, minute_of_day, "test_dest", apply_grace_period=True)
    
    # Should not execute because the ID should be the same
    assert len(matched) == 0, "Identical schedules should have the same ID and not execute twice"

def test_grace_period_behavior():
    """Test that grace period only applies when apply_grace_period=True."""
    # Set up a test schedule
    schedule = {
        "time": "08:00",
        "repeat_schedule": {
            "every": "45",
            "until": "23:00"
        }
    }
    
    # Setup: Current time is 08:03, which is 3 minutes after 08:00 interval
    test_time = datetime(2025, 1, 1, 8, 3, 0)
    minute_of_day = test_time.hour * 60 + test_time.minute
    
    # When apply_grace_period=True (init case), it should execute
    matched = process_time_schedules([schedule], test_time, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 1, "Should execute when apply_grace_period=True for missed event within grace period"
    
    # Clear tracking state before next test
    last_trigger_executions["test_dest"].clear()
    
    # When apply_grace_period=False (load case), it should NOT execute
    matched = process_time_schedules([schedule], test_time, minute_of_day, "test_dest", apply_grace_period=False)
    assert len(matched) == 0, "Should NOT execute when apply_grace_period=False, even for recent missed events"
    
    # Try with a time outside grace period (6 minutes) - should not execute regardless of flag
    late_time = datetime(2025, 1, 1, 8, 6, 0)
    minute_of_day = late_time.hour * 60 + late_time.minute
    
    matched = process_time_schedules([schedule], late_time, minute_of_day, "test_dest", apply_grace_period=True)
    assert len(matched) == 0, "Should not execute when time is outside grace period (even with apply_grace_period=True)" 