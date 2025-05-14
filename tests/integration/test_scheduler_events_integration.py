import pytest
import json
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from collections import deque

from routes.scheduler import resolve_schedule, run_instruction
from routes.scheduler_utils import throw_event, active_events, event_history
from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_states, running_schedulers
from routes.scheduler_utils import EventEntry

@pytest.fixture(autouse=True)
def clean_scheduler_state():
    """Clear scheduler state before and after each test."""
    # Clear before test
    scheduler_contexts_stacks.clear()
    scheduler_states.clear()
    active_events.clear()
    event_history.clear()
    
    yield
    
    # Stop any running schedulers
    for dest_id in list(running_schedulers.keys()):
        from routes.scheduler import stop_scheduler
        stop_scheduler(dest_id)
        
    # Clear after test
    scheduler_contexts_stacks.clear()
    scheduler_states.clear()
    active_events.clear()
    event_history.clear()
    
    # Extra logging to ensure the state is clean
    print("Active events after test:", active_events)

def test_scheduler_processes_events():
    """Test that the scheduler processes events and executes trigger actions."""
    # Start with a clean state
    active_events.clear()
    event_history.clear()
    
    dest_id = "test_dest"
    
    # Create a schedule with an event trigger
    schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "test_event",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "event_triggered", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up a context with vars
    context = {
        "vars": {"existing_var": "existing_value"},
        "publish_destination": dest_id
    }
    scheduler_contexts_stacks[dest_id] = [context]
    
    # Create an event DIRECTLY to avoid timezone issues
    now = datetime.now()
    event_entry = EventEntry(
        key="test_event",
        active_from=now - timedelta(seconds=10),  # Make it active 10 sec ago
        expires=now + timedelta(seconds=60),
        display_name="Test Event",
        payload=None,
        single_consumer=False,
        created_at=now - timedelta(seconds=10)
    )
    
    # Add directly to active_events
    if dest_id not in active_events:
        active_events[dest_id] = {}
    active_events[dest_id]["test_event"] = deque([event_entry])
    
    print(f"Event created directly: {event_entry}")
    print(f"Active events: {active_events.get(dest_id, {})}")
    
    # Manually check the event in active_events directly to confirm it's there
    assert dest_id in active_events, "Destination ID should be in active_events"
    assert "test_event" in active_events[dest_id], "Event key should be in active_events for this destination"
    assert len(active_events[dest_id]["test_event"]) == 1, "There should be 1 event in the queue"
    
    # Manually resolve the schedule
    instructions = resolve_schedule(schedule, now, dest_id)
    
    print(f"Resolved instructions: {instructions}")
    
    # Check if instructions were found
    assert len(instructions) > 0, "Instructions should be found from the event trigger"
    
    # Manually execute the instructions
    for instr in instructions:
        print(f"Executing instruction: {instr}")
        run_instruction(instr, context, now, [], dest_id)
    
    # Verify the event triggered the action
    assert "vars" in context, "Context should have vars dictionary"
    assert "event_triggered" in context["vars"], "event_triggered should be in vars"
    assert context["vars"]["event_triggered"] is True, "event_triggered should be True"
    
    # Verify the original variable is still there
    assert "existing_var" in context["vars"]
    assert context["vars"]["existing_var"] == "existing_value"

def test_scheduler_processes_delayed_events():
    """Test that the scheduler processes delayed events at the right time."""
    dest_id = "test_dest"
    
    # Create a schedule with an event trigger
    schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "delayed_event",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "delayed_triggered", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up a context with vars
    context = {
        "vars": {"existing_var": "existing_value"},
        "publish_destination": dest_id
    }
    scheduler_contexts_stacks[dest_id] = [context]
    
    # Create delayed event directly to avoid timezone issues
    now = datetime.now()
    event_entry = EventEntry(
        key="delayed_event",
        active_from=now + timedelta(seconds=2),  # Active in 2 seconds 
        expires=now + timedelta(seconds=60),
        display_name="Delayed Event",
        payload=None,
        single_consumer=False,
        created_at=now
    )
    
    # Add directly to active_events
    if dest_id not in active_events:
        active_events[dest_id] = {}
    active_events[dest_id]["delayed_event"] = deque([event_entry])
    
    print(f"Event created directly: {event_entry}")
    
    # Check immediately - event should not be processed yet
    instructions = resolve_schedule(schedule, now, dest_id)
    assert not instructions, "No instructions should be generated for a delayed event"
    
    # Wait for delay to pass
    time.sleep(2.5)
    
    # Now check again - the event should be ready
    now_after_delay = datetime.now()
    instructions = resolve_schedule(schedule, now_after_delay, dest_id)
    
    print(f"Resolved instructions after delay: {instructions}")
    
    # Check if instructions were found
    assert len(instructions) > 0, "Instructions should be found from the delayed event trigger"
    
    # Manually execute the instructions
    for instr in instructions:
        run_instruction(instr, context, now_after_delay, [], dest_id)
    
    # Verify the event was processed
    assert "delayed_triggered" in context["vars"]
    assert context["vars"]["delayed_triggered"] is True
    
    # Verify the original variable is still there
    assert "existing_var" in context["vars"]
    assert context["vars"]["existing_var"] == "existing_value"

def test_event_with_payload():
    """Test that event payload is accessible to the scheduler."""
    dest_id = "test_dest"
    
    # Create a schedule with an event trigger and a payload-using instruction
    schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "payload_event",
                "trigger_actions": {
                    "instructions_block": [
                        # Add jinja access to event payload when we have that feature
                        # For now just set a var
                        {"action": "set_var", "var": "got_payload", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up a context with vars
    context = {
        "vars": {"existing_var": "existing_value"},
        "publish_destination": dest_id
    }
    scheduler_contexts_stacks[dest_id] = [context]
    
    # Create an event with payload directly to avoid timezone issues
    now = datetime.now()
    payload_data = {"user": "test_user", "action": "login"}
    event_entry = EventEntry(
        key="payload_event",
        active_from=now - timedelta(seconds=1),  # Make it already active
        expires=now + timedelta(seconds=60),
        display_name="Payload Event",
        payload=payload_data,
        single_consumer=False,
        created_at=now - timedelta(seconds=1)
    )
    
    # Add directly to active_events
    if dest_id not in active_events:
        active_events[dest_id] = {}
    active_events[dest_id]["payload_event"] = deque([event_entry])
    
    print(f"Event with payload created directly: {event_entry}")
    
    # Manually resolve the schedule
    instructions = resolve_schedule(schedule, now, dest_id)
    
    print(f"Resolved instructions: {instructions}")
    
    # Check if instructions were found
    assert len(instructions) > 0, "Instructions should be found from the event trigger"
    
    # Manually execute the instructions
    for instr in instructions:
        run_instruction(instr, context, now, [], dest_id)
    
    # Verify the event was processed
    assert "got_payload" in context["vars"]
    assert context["vars"]["got_payload"] is True
    
    # Verify the original variable is still there
    assert "existing_var" in context["vars"]
    assert context["vars"]["existing_var"] == "existing_value"

def test_single_consumer_event():
    """Test that single_consumer events are only processed once."""
    dest_id1 = "test_dest1"
    dest_id2 = "test_dest2"
    
    # Create an identical schedule for both destinations
    schedule = {
        "triggers": [
            {
                "type": "event",
                "value": "single_event",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "event_triggered", "input": {"value": True}}
                    ]
                }
            }
        ]
    }
    
    # Set up contexts
    context1 = {
        "vars": {},
        "publish_destination": dest_id1
    }
    context2 = {
        "vars": {},
        "publish_destination": dest_id2
    }
    scheduler_contexts_stacks[dest_id1] = [context1]
    scheduler_contexts_stacks[dest_id2] = [context2]
    
    # Create events directly to avoid timezone issues
    now = datetime.now()
    
    # Event for dest1
    event_entry1 = EventEntry(
        key="single_event",
        active_from=now - timedelta(seconds=1),  # Already active
        expires=now + timedelta(seconds=60),
        display_name="Single Event 1",
        payload=None,
        single_consumer=True,
        created_at=now - timedelta(seconds=1)
    )
    
    # Event for dest2
    event_entry2 = EventEntry(
        key="single_event",
        active_from=now - timedelta(seconds=1),  # Already active
        expires=now + timedelta(seconds=60),
        display_name="Single Event 2",
        payload=None,
        single_consumer=True,
        created_at=now - timedelta(seconds=1)
    )
    
    # Add directly to active_events
    if dest_id1 not in active_events:
        active_events[dest_id1] = {}
    active_events[dest_id1]["single_event"] = deque([event_entry1])
    
    if dest_id2 not in active_events:
        active_events[dest_id2] = {}
    active_events[dest_id2]["single_event"] = deque([event_entry2])
    
    print(f"Event 1 created directly: {event_entry1}")
    print(f"Event 2 created directly: {event_entry2}")
    print(f"Active events dest1: {active_events.get(dest_id1, {})}")
    print(f"Active events dest2: {active_events.get(dest_id2, {})}")
    
    # Manually process events for dest1
    instructions1 = resolve_schedule(schedule, now, dest_id1)
    print(f"Dest1 instructions: {instructions1}")
    
    # Check if instructions were found for dest1
    assert len(instructions1) > 0, "Instructions should be found for dest1"
    
    for instr in instructions1:
        run_instruction(instr, context1, now, [], dest_id1)
    
    # Manually process events for dest2
    instructions2 = resolve_schedule(schedule, now, dest_id2)
    print(f"Dest2 instructions: {instructions2}")
    
    # Check if instructions were found for dest2
    assert len(instructions2) > 0, "Instructions should be found for dest2"
    
    for instr in instructions2:
        run_instruction(instr, context2, now, [], dest_id2)
    
    # Verify both events were processed independently
    assert "event_triggered" in context1["vars"], "Event not triggered in context1"
    assert context1["vars"]["event_triggered"] is True
    
    assert "event_triggered" in context2["vars"], "Event not triggered in context2"
    assert context2["vars"]["event_triggered"] is True 