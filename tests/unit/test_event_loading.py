import pytest
import json
import os
from datetime import datetime, timedelta
from collections import deque
from routes.scheduler_utils import EventEntry, active_events, event_history
from routes.scheduler_utils import load_scheduler_state, save_scheduler_state
from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_states

@pytest.fixture
def temp_state_path(monkeypatch, tmpdir):
    """Create a temporary path for scheduler state files and mock the path function."""
    temp_dir = tmpdir.mkdir("scheduler")
    
    # Mock the path function to use our temp directory
    def mock_get_path(publish_destination):
        return os.path.join(temp_dir, f"{publish_destination}.json")
    
    monkeypatch.setattr('routes.scheduler_utils.get_scheduler_storage_path', mock_get_path)
    
    return temp_dir

def test_save_and_load_events(temp_state_path):
    """Test that events are correctly saved and loaded."""
    dest_id = "test_destination"
    
    # Clear global state
    active_events.clear()
    event_history.clear()
    scheduler_contexts_stacks.clear()
    scheduler_states.clear()
    
    # Setup test event
    now = datetime.utcnow()
    event_entry = EventEntry(
        key="test_event",
        active_from=now,
        expires=now + timedelta(minutes=60),
        display_name="Test Event",
        payload={"test": "data"},
        single_consumer=True
    )
    
    # Add to active events
    active_events[dest_id] = {"test_event": deque([event_entry])}
    
    # Add to history
    event_history[dest_id] = [event_entry]
    
    # Set up basic context and state
    scheduler_contexts_stacks[dest_id] = [{"vars": {}}]
    scheduler_states[dest_id] = "running"
    
    # Save state
    save_scheduler_state(dest_id)
    
    # Clear state before loading
    active_events.clear()
    event_history.clear()
    scheduler_contexts_stacks.clear()
    scheduler_states.clear()
    
    # Load state
    loaded_state = load_scheduler_state(dest_id)
    
    # Verify events were loaded correctly
    assert dest_id in active_events
    assert "test_event" in active_events[dest_id]
    assert len(active_events[dest_id]["test_event"]) == 1
    
    loaded_event = list(active_events[dest_id]["test_event"])[0]
    assert isinstance(loaded_event, EventEntry)
    assert loaded_event.key == "test_event"
    assert loaded_event.display_name == "Test Event"
    assert loaded_event.payload == {"test": "data"}
    assert loaded_event.single_consumer is True
    
    # Verify history
    assert dest_id in event_history
    assert len(event_history[dest_id]) == 1
    
    history_event = event_history[dest_id][0]
    assert isinstance(history_event, EventEntry)
    assert history_event.key == "test_event"
    
    # Verify other state
    assert scheduler_states[dest_id] == "running"
    assert len(scheduler_contexts_stacks[dest_id]) == 1

def test_legacy_event_migration(temp_state_path):
