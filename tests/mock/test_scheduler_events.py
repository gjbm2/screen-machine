import pytest
from datetime import datetime
from routes.scheduler import resolve_schedule
from routes.scheduler_utils import active_events

def test_event_trigger_activation(clean_scheduler_state):
    """Test that event triggers activate when events are present."""
    dest_id = "test_dest"
    
    # Create a test schedule with an event trigger
    test_schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "UserInteractedWithScreen",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "triggered", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up an active event
    if dest_id not in active_events:
        active_events[dest_id] = {}
    
    active_events[dest_id]["UserInteractedWithScreen"] = datetime.now()
    
    # Resolve the schedule
    now = datetime.now()
    instructions = resolve_schedule(test_schedule, now, dest_id)
    
    # Verify instructions were generated
    assert len(instructions) == 1
    assert instructions[0]["action"] == "set_var"
    assert instructions[0]["var"] == "triggered"
    
    # Verify the event was consumed (removed)
    assert "UserInteractedWithScreen" not in active_events[dest_id]

def test_multiple_event_triggers(clean_scheduler_state):
    """Test that multiple event triggers can be defined and used."""
    dest_id = "test_dest"
    
    # Create a test schedule with multiple event triggers
    test_schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "UserInteractedWithScreen",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "user_interaction", "input": {"value": True}}
                    ]
                }
            },
            {
                "type": "event",
                "value": "NewSlackMessageDetected",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "slack_message", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up active events
    if dest_id not in active_events:
        active_events[dest_id] = {}
    
    # Add both events
    active_events[dest_id]["UserInteractedWithScreen"] = datetime.now()
    active_events[dest_id]["NewSlackMessageDetected"] = datetime.now()
    
    # Resolve the schedule
    now = datetime.now()
    instructions = resolve_schedule(test_schedule, now, dest_id)
    
    # Verify both sets of instructions were generated
    assert len(instructions) == 2
    
    # Check variables that will be set
    vars_to_set = [instr["var"] for instr in instructions if instr["action"] == "set_var"]
    assert "user_interaction" in vars_to_set
    assert "slack_message" in vars_to_set
    
    # Verify events were consumed
    assert "UserInteractedWithScreen" not in active_events[dest_id]
    assert "NewSlackMessageDetected" not in active_events[dest_id]

def test_event_trigger_priority_over_date_time(clean_scheduler_state):
    """Test that event triggers take priority regardless of other triggers."""
    dest_id = "test_dest"
    
    # Get the current date and day for matching triggers
    now = datetime.now()
    date_str = now.strftime("%-d-%b")  # e.g., 25-Dec
    day_str = now.strftime("%A")       # e.g., Friday
    time_str = now.strftime("%H:%M")   # e.g., 08:00
    
    # Create a test schedule with date, day, and event triggers
    test_schedule = {
        "triggers": [
            {
                "type": "date",
                "date": date_str,
                "scheduled_actions": [
                    {
                        "time": time_str,
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "date_triggered", "input": {"value": True}}
                            ]
                        }
                    }
                ]
            },
            {
                "type": "day_of_week",
                "days": [day_str],
                "scheduled_actions": [
                    {
                        "time": time_str,
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "day_triggered", "input": {"value": True}}
                            ]
                        }
                    }
                ]
            },
            {
                "type": "event",
                "value": "Poke",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "event_triggered", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up an active event
    if dest_id not in active_events:
        active_events[dest_id] = {}
    
    active_events[dest_id]["Poke"] = now
    
    # Resolve the schedule
    instructions = resolve_schedule(test_schedule, now, dest_id)
    
    # Verify instructions were generated for the event (and possibly the matching date/day)
    event_triggered = False
    for instr in instructions:
        if instr["action"] == "set_var" and instr["var"] == "event_triggered":
            event_triggered = True
            break
    
    assert event_triggered, "The event trigger should have generated instructions"
    
    # Verify the event was consumed
    assert "Poke" not in active_events[dest_id]

def test_no_matching_events(clean_scheduler_state):
    """Test behavior when there are event triggers but no matching events."""
    dest_id = "test_dest"
    
    # Create a test schedule with an event trigger
    test_schedule = {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initial_ran", "input": {"value": True}}
            ]
        },
        "triggers": [
            {
                "type": "event",
                "value": "UserInteractedWithScreen",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "triggered", "input": {"value": True}}
                    ]
                }
            }
        ],
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "final_ran", "input": {"value": True}}
            ]
        }
    }
    
    # Ensure no active events
    if dest_id in active_events:
        active_events[dest_id] = {}
    
    # Resolve the schedule
    now = datetime.now()
    instructions = resolve_schedule(test_schedule, now, dest_id)
    
    # Verify no trigger instructions were generated
    # If no triggers match, we should get the final_actions
    assert len(instructions) > 0
    
    # Check that we got final_actions instructions
    vars_to_set = [instr["var"] for instr in instructions if instr["action"] == "set_var"]
    assert "final_ran" in vars_to_set
    assert "triggered" not in vars_to_set  # The event trigger shouldn't run 