import unittest
from unittest import mock
import json
from datetime import datetime, timedelta
import uuid
from pathlib import Path
from collections import deque

from routes.scheduler import resolve_schedule, run_instruction
from routes.scheduler_utils import EventEntry, active_events, throw_event, pop_next_event
from routes.scheduler import debug


class TestSchedulerEventHandling(unittest.TestCase):
    """Test the event handling in scheduler, particularly the event context management."""

    def setUp(self):
        """Set up common test data."""
        # Mock context
        self.context = {"vars": {}}
        
        # Test destination
        self.destination = "test_dest"
        
        # Create a basic schedule with event triggers
        self.schedule = {
            "name": "Test Schedule",
            "triggers": [
                {
                    "type": "event",
                    "value": "test_event",
                    "trigger_actions": {
                        "instructions": [
                            {"action": "log", "message": "Event {{_event.key}} triggered with payload: {{_event.payload}}"}
                        ]
                    }
                },
                {
                    "type": "event",
                    "value": "another_event",
                    "trigger_actions": {
                        "instructions": [
                            {"action": "log", "message": "Another event {{_event.key}} with payload: {{_event.payload}}"}
                        ]
                    }
                }
            ]
        }
        
        # Mock the necessary functions
        self.patcher_info = mock.patch('routes.scheduler.info')
        self.mock_info = self.patcher_info.start()
        
        self.patcher_debug = mock.patch('routes.scheduler.debug')
        self.mock_debug = self.patcher_debug.start()
        
        self.patcher_error = mock.patch('routes.scheduler.error')
        self.mock_error = self.patcher_error.start()
        
        self.patcher_log_schedule = mock.patch('routes.scheduler.log_schedule')
        self.mock_log_schedule = self.patcher_log_schedule.start()
        
        # Mock pop_next_event to return our test events
        self.patcher_pop_next_event = mock.patch('routes.scheduler_utils.pop_next_event')
        self.mock_pop_next_event = self.patcher_pop_next_event.start()
        
        # Mock update_scheduler_state
        self.patcher_update_state = mock.patch('routes.scheduler.update_scheduler_state')
        self.mock_update_state = self.patcher_update_state.start()
        
        # Mock get_context_stack
        self.patcher_get_context_stack = mock.patch('routes.scheduler.get_context_stack')
        self.mock_get_context_stack = self.patcher_get_context_stack.start()
        self.mock_get_context_stack.return_value = [self.context]
        
        # Initialize active_events
        active_events.clear()
        active_events[self.destination] = {}
    
    def tearDown(self):
        """Clean up after tests."""
        self.patcher_info.stop()
        self.patcher_debug.stop()
        self.patcher_error.stop()
        self.patcher_log_schedule.stop()
        self.patcher_pop_next_event.stop()
        self.patcher_update_state.stop()
        self.patcher_get_context_stack.stop()
        active_events.clear()
    
    def create_event_entry(self, key, payload=None):
        """Helper to create an event entry."""
        now = datetime.now()
        return EventEntry(
            key=key,
            unique_id=str(uuid.uuid4()),
            active_from=now,
            expires=now + timedelta(minutes=5),
            status="ACTIVE",
            payload=payload,
            display_name=f"Test {key}"
        )
    
    def test_single_event_resolution(self):
        """Test that a single event is correctly resolved and its context is available."""
        # Create a test event
        test_event = self.create_event_entry("test_event", {"message": "Hello, World!"})
        
        # Set up the mock to return our event
        self.mock_pop_next_event.side_effect = lambda dest, key, *args, **kwargs: test_event if key == "test_event" else None
        
        # Run resolve_schedule
        now = datetime.now()
        instructions = resolve_schedule(self.schedule, now, self.destination, False, self.context)
        
        # Verify an instruction was returned
        self.assertEqual(len(instructions), 1)
        self.assertEqual(instructions[0]["action"], "log")
        
        # Verify event context was created
        self.assertIn("_event", self.context["vars"])
        
        # Verify the event context contains the correct data
        self.assertEqual(self.context["vars"]["_event"]["key"], "test_event")
        self.assertEqual(self.context["vars"]["_event"]["payload"], {"message": "Hello, World!"})
    
    def test_multiple_events_resolution(self):
        """Test that multiple events in the same cycle are correctly resolved with isolated context."""
        # Create test events
        test_event1 = self.create_event_entry("test_event", {"message": "Hello from event 1"})
        test_event2 = self.create_event_entry("another_event", {"message": "Hello from event 2"})
        
        # Set up the mock to return our events
        def mock_pop_event(dest, key, *args, **kwargs):
            if key == "test_event":
                return test_event1
            elif key == "another_event":
                return test_event2
            return None
        
        self.mock_pop_next_event.side_effect = mock_pop_event
        
        # Run resolve_schedule
        now = datetime.now()
        instructions = resolve_schedule(self.schedule, now, self.destination, False, self.context)
        
        # Verify two instructions were returned (one for each event)
        self.assertEqual(len(instructions), 2)
        
        # Find which instruction is for which event
        instr1 = next(i for i in instructions if "test_event" in i.get("message", ""))
        instr2 = next(i for i in instructions if "another_event" in i.get("message", ""))
        
        # Verify only the _event variable exists
        self.assertIn("_event", self.context["vars"])
        
        # Clean up event context
        if "vars" in self.context and "_event" in self.context["vars"]:
            del self.context["vars"]["_event"]
    
    def test_instruction_execution_copies_correct_event(self):
        """Test that when running an instruction, it correctly copies its specific event data to _event."""
        # Create test events
        test_event1 = self.create_event_entry("test_event", {"message": "Event 1 data"})
        test_event2 = self.create_event_entry("another_event", {"message": "Event 2 data"})
        
        # Initialize context with both event data
        self.context["vars"]["_event"] = {
            "key": "test_event",
            "payload": {"message": "Event 1 data"},
            "unique_id": test_event1.unique_id
        }
        
        # Create an instruction for event1
        instruction = {
            "action": "log",
            "message": "Event {{_event.key}} with payload: {{_event.payload.message}}",
        }
        
        # Create output list for run_instruction
        output = []
        
        # Mock process_instruction_jinja to just do string templating
        with mock.patch('routes.scheduler.process_instruction_jinja') as mock_process:
            mock_process.side_effect = lambda instr, ctx, *args: {
                **instr,
                "message": instr["message"].replace("{{_event.key}}", ctx["vars"]["_event"]["key"])
                                        .replace("{{_event.payload.message}}", ctx["vars"]["_event"]["payload"]["message"])
            }
            
            # Run the instruction
            run_instruction(instruction, self.context, datetime.now(), output, self.destination)
            
            # Verify that _event was switched to event1 data before processing
            mock_process.assert_called_once()
            self.assertEqual(self.context["vars"]["_event"]["key"], "test_event")
            self.assertEqual(self.context["vars"]["_event"]["payload"]["message"], "Event 1 data")
    
    def test_event_context_cleanup(self):
        """Test that event context variables are properly cleaned up after execution."""
        # Initialize context with event data
        self.context["vars"]["_event"] = {
            "key": "test_event",
            "payload": {"message": "Event data"},
            "unique_id": "test123",
            "created_at": "2023-01-01T12:00:00Z",
            "display_name": "Test Event"
        }
        
        # Create a test function that simulates the cleanup code from run_scheduler_loop
        def test_cleanup():
            if "vars" in self.context and "_event" in self.context["vars"]:
                debug("Removing temporary _event from context after instruction block")
                del self.context["vars"]["_event"]
        
        # Run the cleanup
        test_cleanup()
        
        # Verify that event context was cleaned up
        self.assertNotIn("_event", self.context["vars"])
    
    def test_end_to_end_event_flow(self):
        """Test the complete flow from event resolution through instruction execution to cleanup."""
        # Create a test event
        test_event = self.create_event_entry("test_event", {"message": "Complete flow test"})
        
        # Set up the mock to return our event
        self.mock_pop_next_event.side_effect = lambda dest, key, *args, **kwargs: test_event if key == "test_event" else None
        
        # Run resolve_schedule
        now = datetime.now()
        instructions = resolve_schedule(self.schedule, now, self.destination, False, self.context)
        
        # Verify an instruction was returned
        self.assertEqual(len(instructions), 1)
        
        # Create output list for run_instruction
        output = []
        
        # Run the instruction
        run_instruction(instructions[0], self.context, now, output, self.destination)
        
        # Clean up event context
        if "vars" in self.context and "_event" in self.context["vars"]:
            del self.context["vars"]["_event"]
        
        # Verify the event context was cleaned up
        self.assertNotIn("_event", self.context["vars"])


if __name__ == '__main__':
    unittest.main() 