import unittest
import asyncio
import json
from unittest import mock
from datetime import datetime, timedelta, timezone
import time
import uuid
import os
from pathlib import Path
from collections import deque

from routes.scheduler import start_scheduler, stop_scheduler, running_schedulers
from routes.scheduler_utils import (
    EventEntry, active_events, event_history, throw_event, check_all_expired_events, 
    get_scheduler_storage_path
)


class TestSchedulerEventIntegration(unittest.TestCase):
    """Integration test for scheduler event handling."""
    
    def setUp(self):
        """Set up tests with mocked environment."""
        # Create test directory
        self.test_dir = Path("test_scheduler_event_integration")
        self.test_dir.mkdir(exist_ok=True)
        
        # Create a file for the scheduler state
        self.state_file = self.test_dir / "scheduler_state.json"
        self.state_file.touch()
        
        # Initialize state file with empty state
        with open(self.state_file, 'w') as f:
            json.dump({
                "state": "stopped",
                "schedule_stack": [],
                "context_stack": [{"vars": {}}]
            }, f)
        
        # Mock the scheduler storage path
        self.storage_patcher = mock.patch('routes.scheduler_utils.get_scheduler_storage_path')
        self.mock_storage = self.storage_patcher.start()
        self.mock_storage.return_value = str(self.state_file)
        
        # Mock the log functions
        self.info_patcher = mock.patch('routes.scheduler.info')
        self.mock_info = self.info_patcher.start()
        
        self.debug_patcher = mock.patch('routes.scheduler.debug')
        self.mock_debug = self.debug_patcher.start()
        
        self.error_patcher = mock.patch('routes.scheduler.error')
        self.mock_error = self.error_patcher.start()
        
        # Clear global state
        active_events.clear()
        event_history.clear()
        for dest in list(running_schedulers.keys()):
            stop_scheduler(dest)
        
        # Create test destination
        self.destination = "test_integration_dest"
        
        # Create a test schedule with event triggers
        self.test_schedule = {
            "name": "Integration Test Schedule",
            "triggers": [
                {
                    "type": "event",
                    "value": "event_one",
                    "trigger_actions": {
                        "instructions": [
                            {
                                "action": "set_var",
                                "var_name": "last_event_one",
                                "value": "{{_event.payload.value}} at {{_event.created_at}}"
                            }
                        ]
                    }
                },
                {
                    "type": "event",
                    "value": "event_two",
                    "trigger_actions": {
                        "instructions": [
                            {
                                "action": "set_var",
                                "var_name": "last_event_two",
                                "value": "{{_event.payload.value}} at {{_event.created_at}}"
                            }
                        ]
                    }
                }
            ]
        }
    
    def tearDown(self):
        """Clean up after tests."""
        # Stop any running schedulers
        for dest in list(running_schedulers.keys()):
            stop_scheduler(dest)
        
        # Clean up mocks
        self.storage_patcher.stop()
        self.info_patcher.stop()
        self.debug_patcher.stop()
        self.error_patcher.stop()
        
        # Remove test files
        if self.state_file.exists():
            self.state_file.unlink()
        if self.test_dir.exists():
            self.test_dir.rmdir()
        
        # Clean global state
        active_events.clear()
        event_history.clear()
    
    def wait_for_scheduler_cycle(self, cycles=1):
        """Wait for the scheduler to complete a few cycles."""
        # Each cycle takes about 2 seconds
        time.sleep(2.0 * cycles + 0.5)  # Add a little buffer
    
    def get_context_from_scheduler(self, dest):
        """Extract the current context from a running scheduler."""
        if dest not in running_schedulers:
            return None
        
        # Note: In production code, you'd use the proper API
        # For tests, we'll read from the file directly
        path = Path(get_scheduler_storage_path(dest))
        if not path.exists():
            return None
        
        with open(path) as f:
            state = json.load(f)
        
        if not state or 'context_stack' not in state or not state['context_stack']:
            return None
        
        # Return the top context
        return state['context_stack'][-1]
    
    def test_multiple_events_variables(self):
        """Test that multiple events correctly update separate variables in the context."""
        # Start the scheduler with our test schedule
        start_scheduler(self.destination, self.test_schedule)
        self.wait_for_scheduler_cycle()
        
        try:
            # Throw the first event
            throw_event(
                key="event_one",
                scope=self.destination,
                payload={"value": "First event data"},
                ttl="60s"
            )
            
            # Wait for processing
            self.wait_for_scheduler_cycle()
            
            # Throw the second event
            throw_event(
                key="event_two",
                scope=self.destination,
                payload={"value": "Second event data"},
                ttl="60s"
            )
            
            # Wait for processing
            self.wait_for_scheduler_cycle()
            
            # Get the context and verify both events were processed
            context = self.get_context_from_scheduler(self.destination)
            self.assertIsNotNone(context)
            self.assertIn("vars", context)
            
            # Make sure each event updated its own variable
            self.assertIn("last_event_one", context["vars"])
            self.assertIn("First event data", context["vars"]["last_event_one"])
            
            self.assertIn("last_event_two", context["vars"])
            self.assertIn("Second event data", context["vars"]["last_event_two"])
            
            # Make sure _event is not in the context (should be removed after processing)
            self.assertNotIn("_event", context["vars"])
            
            # Verify events were added to history
            assert self.destination in event_history
            assert len(event_history[self.destination]) == 2
            assert event_history[self.destination][0].key == "event_one"
            assert event_history[self.destination][1].key == "event_two"
            
        finally:
            # Clean up
            stop_scheduler(self.destination)
    
    def test_rapid_fire_events(self):
        """Test that rapidly firing multiple events are all processed correctly."""
        # Start the scheduler with our test schedule
        start_scheduler(self.destination, self.test_schedule)
        self.wait_for_scheduler_cycle()
        
        try:
            # Throw multiple events in rapid succession
            # These should all be queued and processed in order
            for i in range(5):
                throw_event(
                    key="event_one",
                    scope="dest",
                    dest_id=self.destination,
                    payload={"value": f"Rapid event #{i+1}"},
                    ttl="60s"
                )
            
            # Wait for processing all events
            self.wait_for_scheduler_cycle(3)  # Give it more time
            
            # Get the context and check the last event value
            context = self.get_context_from_scheduler(self.destination)
            self.assertIsNotNone(context)
            self.assertIn("vars", context)
            self.assertIn("last_event_one", context["vars"])
            self.assertIn("Rapid event #5", context["vars"]["last_event_one"])
            
            # Check the event history - there should be 5 consumed events
            # One for each of our event_one events
            self.assertIn(self.destination, event_history)
            consumed_events = [e for e in event_history[self.destination] 
                              if e.key == "event_one" and e.status == "CONSUMED"]
            self.assertEqual(len(consumed_events), 5)
            
        finally:
            # Clean up
            stop_scheduler(self.destination)
    
    def test_event_payload_access(self):
        """Test that event payloads are correctly accessible in instructions."""
        # Start the scheduler with our test schedule
        start_scheduler(self.destination, self.test_schedule)
        self.wait_for_scheduler_cycle()
        
        try:
            # Throw an event with a complex payload
            complex_payload = {
                "nested": {
                    "data": {
                        "value": "test_value",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                },
                "array": [1, 2, 3],
                "boolean": True
            }
            
            throw_event(
                key="event_one",
                scope="dest",
                dest_id=self.destination,
                payload=complex_payload,
                ttl="60s"
            )
            
            # Wait for processing
            self.wait_for_scheduler_cycle()
            
            # Get the context and verify the payload was processed
            context = self.get_context_from_scheduler(self.destination)
            self.assertIsNotNone(context)
            self.assertIn("vars", context)
            self.assertIn("last_event_one", context["vars"])
            
            # The value should contain the nested data
            value = context["vars"]["last_event_one"]
            self.assertIn("test_value", value)
            
            # Verify event was added to history
            assert self.destination in event_history
            assert len(event_history[self.destination]) == 1
            assert event_history[self.destination][0].key == "event_one"
            
        finally:
            # Clean up
            stop_scheduler(self.destination)


if __name__ == '__main__':
    unittest.main() 