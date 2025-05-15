import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from routes.scheduler_utils import (
    EventEntry, throw_event, pop_next_event, 
    active_events, event_history,
    get_events_for_destination, clear_events_for_destination,
    parse_ttl, parse_time
)
from collections import deque

@pytest.fixture(autouse=True)
def clear_event_state():
    """Clear event state before and after each test."""
    # Clear events before test
    active_events.clear()
    event_history.clear()
    
    yield
    
    # Clear events after test
    active_events.clear()
    event_history.clear()

def test_parse_ttl():
    """Test TTL parsing function."""
    assert parse_ttl("30s") == 30
    assert parse_ttl("5m") == 300
    assert parse_ttl("2h") == 7200
    assert parse_ttl("1d") == 86400
    assert parse_ttl(60) == 60
    assert parse_ttl(None, default=120) == 120
    assert parse_ttl("invalid", default=45) == 45

def test_parse_time():
    """Test time parsing function."""
    now = datetime.now()
    
    # Test with datetime object
    assert parse_time(now) == now
    
    # Test with ISO string
    iso_time = "2024-06-01T12:00:00"
    parsed = parse_time(iso_time)
    assert parsed is not None
    assert parsed.year == 2024
    assert parsed.month == 6
    assert parsed.day == 1
    assert parsed.hour == 12
    
    # Test with invalid input
    assert parse_time("invalid") is None
    assert parse_time(None) is None

def test_throw_event_to_destination():
    """Test throwing an event to a specific destination."""
    # Throw event to a destination
    result = throw_event(
        scope="dest",
        key="test_event",
        ttl="60s",
        dest_id="test_dest"
    )
    
    # Verify result
    assert result["status"] == "queued"
    assert result["key"] == "test_event"
    assert "test_dest" in result["destinations"]
    
    # Verify event was stored
    assert "test_dest" in active_events
    assert "test_event" in active_events["test_dest"]
    assert len(active_events["test_dest"]["test_event"]) == 1
    
    # Verify history
    assert "test_dest" in event_history
    assert len(event_history["test_dest"]) == 1

def test_throw_event_with_display_name_and_payload():
    """Test throwing an event with display name and payload."""
    payload = {"user": "test_user", "action": "login"}
    result = throw_event(
        scope="dest",
        key="user_login",
        ttl="60s",
        dest_id="test_dest",
        display_name="User Login Event",
        payload=payload
    )
    
    # Verify event was stored with display_name and payload
    event_entry = active_events["test_dest"]["user_login"][0]
    assert event_entry.display_name == "User Login Event"
    assert event_entry.payload == payload

def test_throw_event_to_group():
    """Test throwing an event to a group (which fans out to destinations)."""
    # Mock the group membership function
    with patch('routes.scheduler_utils.get_destinations_for_group') as mock_get_dests:
        mock_get_dests.return_value = ["dest1", "dest2", "dest3"]
        
        result = throw_event(
            scope="group",
            key="group_event",
            ttl="60s",
            group_id="test_group"
        )
        
        # Verify result
        assert result["status"] == "queued"
        assert "group" in result
        assert result["group"] == "test_group"
        
        # Verify event was stored for all destinations in the group
        for dest in ["dest1", "dest2", "dest3"]:
            assert dest in active_events
            assert "group_event" in active_events[dest]
            assert len(active_events[dest]["group_event"]) == 1

def test_throw_event_globally():
    """Test throwing an event globally (to all destinations)."""
    # Mock the all destinations function
    with patch('routes.scheduler_utils.all_destinations') as mock_all_dests:
        mock_all_dests.return_value = ["dest1", "dest2", "dest3"]
        
        result = throw_event(
            scope="global",
            key="global_event",
            ttl="60s"
        )
        
        # Verify event was stored for all destinations
        for dest in ["dest1", "dest2", "dest3"]:
            assert dest in active_events
            assert "global_event" in active_events[dest]
            assert len(active_events[dest]["global_event"]) == 1

def test_throw_event_with_delay():
    """Test throwing an event with a delay."""
    # Current time
    now = datetime.utcnow()
    
    result = throw_event(
        scope="dest",
        key="delayed_event",
        ttl="60s",
        delay="10s",
        dest_id="test_dest"
    )
    
    # Verify event was stored with future active_from time
    event_entry = active_events["test_dest"]["delayed_event"][0]
    assert event_entry.active_from > now
    assert event_entry.active_from < now + timedelta(seconds=15)  # Allow a bit of tolerance

def test_throw_event_with_future_time():
    """Test throwing an event with a specific future time."""
    # Set a future time
    future = datetime.utcnow() + timedelta(minutes=5)
    future_iso = future.isoformat()
    
    result = throw_event(
        scope="dest",
        key="future_event",
        ttl="60s",
        future_time=future_iso,
        dest_id="test_dest"
    )
    
    # Verify event was stored with specific future time
    event_entry = active_events["test_dest"]["future_event"][0]
    
    # Convert both to seconds since epoch for easier comparison
    entry_epoch = event_entry.active_from.timestamp()
    future_epoch = future.timestamp()
    assert abs(entry_epoch - future_epoch) < 1  # Allow 1 second tolerance

def test_throw_event_with_single_consumer():
    """Test throwing an event with single_consumer flag."""
    result = throw_event(
        scope="dest",
        key="single_consumer_event",
        ttl="60s",
        dest_id="test_dest",
        single_consumer=True
    )
    
    # Verify event was stored with single_consumer flag
    event_entry = active_events["test_dest"]["single_consumer_event"][0]
    assert event_entry.single_consumer is True

def test_pop_next_event():
    """Test popping the next event."""
    # Throw an event
    throw_event(
        scope="dest",
        key="test_event",
        ttl="60s",
        dest_id="test_dest"
    )
    
    # Pop the event
    event_entry = pop_next_event("test_dest", "test_event")
    
    # Verify event was retrieved
    assert event_entry is not None
    assert event_entry.key == "test_event"
    
    # Verify event was removed from queue
    assert "test_dest" in active_events
    assert "test_event" in active_events["test_dest"]
    assert len(active_events["test_dest"]["test_event"]) == 0

def test_pop_next_event_respect_active_from():
    """Test that pop_next_event respects the active_from time."""
    # Throw a future event
    future = datetime.utcnow() + timedelta(minutes=5)
    throw_event(
        scope="dest",
        key="future_event",
        ttl="60s",
        future_time=future.isoformat(),
        dest_id="test_dest"
    )
    
    # Try to pop the event now (should fail)
    event_entry = pop_next_event("test_dest", "future_event")
    
    # Verify event was not retrieved
    assert event_entry is None
    
    # Verify event is still in queue
    assert "test_dest" in active_events
    assert "future_event" in active_events["test_dest"]
    assert len(active_events["test_dest"]["future_event"]) == 1
    
    # Try to pop with a timestamp after the active_from time
    future_plus = future + timedelta(seconds=10)
    event_entry = pop_next_event("test_dest", "future_event", future_plus)
    
    # Verify event was retrieved this time
    assert event_entry is not None
    assert event_entry.key == "future_event"

def test_get_events_for_destination():
    """Test getting all events for a destination."""
    # Throw a few events
    throw_event(scope="dest", key="event1", ttl="60s", dest_id="test_dest")
    throw_event(scope="dest", key="event2", ttl="60s", dest_id="test_dest")
    throw_event(
        scope="dest", 
        key="event3", 
        ttl="60s", 
        dest_id="test_dest",
        display_name="Event Three",
        payload={"data": "test"}
    )
    
    # Get events
    result = get_events_for_destination("test_dest")
    
    # Verify result structure
    assert "queue" in result
    assert "history" in result
    
    # Verify queue contents
    assert len(result["queue"]) == 3
    
    # Verify event details are included
    event3 = next((e for e in result["queue"] if e["key"] == "event3"), None)
    assert event3 is not None
    assert event3["display_name"] == "Event Three"
    assert event3["has_payload"] is True

def test_clear_events_for_destination():
    """Test clearing events for a destination."""
    # Throw a few events
    throw_event(scope="dest", key="event1", ttl="60s", dest_id="test_dest")
    throw_event(scope="dest", key="event2", ttl="60s", dest_id="test_dest")
    throw_event(scope="dest", key="event3", ttl="60s", dest_id="test_dest")
    
    # Clear specific event
    result = clear_events_for_destination("test_dest", "event1")
    assert result["cleared"] == 1
    
    # Verify only that event was cleared
    assert "event1" in active_events["test_dest"]
    assert len(active_events["test_dest"]["event1"]) == 0
    assert len(active_events["test_dest"]["event2"]) == 1
    assert len(active_events["test_dest"]["event3"]) == 1
    
    # Clear all events
    result = clear_events_for_destination("test_dest")
    assert result["cleared"] == 2
    
    # Verify all events were cleared
    for key in ["event1", "event2", "event3"]:
        assert key in active_events["test_dest"]
        assert len(active_events["test_dest"][key]) == 0

def test_expired_events_cleanup(clear_event_state):
    """Test that expired events are automatically cleaned up."""
    dest_id = "test_dest"
    
    # Current time
    now = datetime.utcnow()
    
    # Add an event that's already expired
    past = now - timedelta(seconds=10)
    
    # Create and manually add an expired event
    expired_entry = EventEntry(
        key="expired_event",
        active_from=past,
        expires=past + timedelta(seconds=5),  # Already expired
        display_name="Expired Event"
    )
    
    if dest_id not in active_events:
        active_events[dest_id] = {}
    if "expired_event" not in active_events[dest_id]:
        active_events[dest_id]["expired_event"] = deque()
        
    active_events[dest_id]["expired_event"].append(expired_entry)
    
    # Try to pop it (should be cleaned up)
    event = pop_next_event(dest_id, "expired_event", now)
    assert event is None
    
    # Verify expired event was removed
    assert "expired_event" in active_events[dest_id] and len(active_events[dest_id]["expired_event"]) == 0 