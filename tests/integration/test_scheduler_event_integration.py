import unittest
import asyncio
import json
from unittest import mock
from datetime import datetime, timedelta
import time
import uuid
import os
from pathlib import Path

from routes.scheduler import start_scheduler, stop_scheduler, running_schedulers
from routes.scheduler_utils import (
    active_events, event_history, throw_event, check_all_expired_events, 
    get_scheduler_storage_path
)


class TestSchedulerEventIntegration(unittest.TestCase):
    """Integration test for scheduler event handling."""
    
    def setUp(self):
        """Set up tests with mocked environment."""
        # Create test directory
        self.test_dir = Path("test_scheduler_event_integration")
        self.test_dir.mkdir(exist_ok=True)
        
        # Mock the scheduler storage path
        self.storage_patcher = mock.patch('routes.scheduler_utils.get_scheduler_storage_path')
        self.mock_storage = self.storage_patcher.start()
        self.mock_storage.return_value = str(self.test_dir)
        
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
                },
                {
                    "type": "event",
                    "value": "event_check",
                    "trigger_actions": {
                        "instructions": [
                            {
                                "action": "set_var",
                                "var_name": "validation_result",
                                "value": "event_one={{vars.last_event_one}}, event_two={{vars.last_event_two}}"
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
        for file in self.test_dir.glob('*'):
            file.unlink()
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
            
            # Make sure no other event variables are in the context
            for key in context["vars"].keys():
                self.assertFalse(key.startswith("_event_"), f"Found unexpected event variable: {key}")
            
            # Now throw a validation event to check that we can access both variables
            throw_event(
                key="event_check",
                scope=self.destination,
                payload={"check": "validation"},
                ttl="60s"
            )
            
            # Wait for processing
            self.wait_for_scheduler_cycle()
            
            # Get the updated context
            context = self.get_context_from_scheduler(self.destination)
            
            # Verify the validation variable contains data from both previous events
            self.assertIn("validation_result", context["vars"])
            validation = context["vars"]["validation_result"]
            self.assertIn("event_one=First event data", validation)
            self.assertIn("event_two=Second event data", validation)
            
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
                    scope=self.destination,
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
        """Test that event payload is correctly accessible from Jinja templates."""
        # Create a schedule with complex payload access
        complex_schedule = {
            "name": "Complex Payload Test",
            "triggers": [
                {
                    "type": "event",
                    "value": "complex_event",
                    "trigger_actions": {
                        "instructions": [
                            {
                                "action": "set_var",
                                "var_name": "item_name",
                                "value": "{{_event.payload.item.name}}"
                            },
                            {
                                "action": "set_var",
                                "var_name": "item_price",
                                "value": "{{_event.payload.item.price}}"
                            },
                            {
                                "action": "set_var",
                                "var_name": "address_city",
                                "value": "{{_event.payload.shipping.address.city}}"
                            }
                        ]
                    }
                }
            ]
        }
        
        # Start the scheduler with the complex schedule
        start_scheduler(self.destination, complex_schedule)
        self.wait_for_scheduler_cycle()
        
        try:
            # Throw an event with a complex nested payload
            throw_event(
                key="complex_event",
                scope=self.destination,
                payload={
                    "item": {
                        "name": "Test Product",
                        "price": 99.95,
                        "in_stock": True
                    },
                    "shipping": {
                        "method": "express",
                        "address": {
                            "street": "123 Test St",
                            "city": "Testville",
                            "zip": "12345"
                        }
                    }
                },
                ttl="60s"
            )
            
            # Wait for processing
            self.wait_for_scheduler_cycle()
            
            # Get the context and verify the complex payload was processed correctly
            context = self.get_context_from_scheduler(self.destination)
            self.assertIsNotNone(context)
            self.assertIn("vars", context)
            
            # Check each extracted value
            self.assertEqual(context["vars"]["item_name"], "Test Product")
            self.assertEqual(context["vars"]["item_price"], "99.95")
            self.assertEqual(context["vars"]["address_city"], "Testville")
            
        finally:
            # Clean up
            stop_scheduler(self.destination)


if __name__ == '__main__':
    unittest.main() 