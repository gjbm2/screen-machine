import pytest
import json
from datetime import datetime, timedelta
from collections import deque
from routes.scheduler_utils import EventEntry, active_events, event_history

@pytest.fixture
def clean_events_state():
    """Reset event-related state before and after tests."""
    # Clear before test
    active_events.clear()
    event_history.clear()
    
    yield
    
    # Clear after test
    active_events.clear()
    event_history.clear()

def test_throw_and_get_event(test_client, clean_events_state):
    """Test throwing an event and retrieving it."""
    dest_id = "test_dest"
    
    # Throw an event using the unified endpoint
    response = test_client.post(
        "/api/schedulers/events/throw",
        json={
            "event": "test_event",
            "display_name": "Test Event",
            "scope": "dest",
            "destination": dest_id,
            "ttl": "300s",
            "payload": {"test": "data"}
        }
    )
    
    # Check response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert data["key"] == "test_event"
    assert dest_id in data["destinations"]
    
    # Get events
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    
    # Check events response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "queue" in data
    assert "history" in data
    
    # Check queue
    assert len(data["queue"]) == 1
    event = data["queue"][0]
    assert event["key"] == "test_event"
    assert event["display_name"] == "Test Event"
    assert event["has_payload"] is True
    
    # Check history
    assert len(data["history"]) == 1
    assert data["history"][0]["key"] == "test_event"
    
    # Also test the unified endpoint with scope parameter
    response = test_client.post(
        "/api/schedulers/events/throw",
        json={
            "event": "direct_event",
            "scope": "dest",
            "destination": dest_id,
            "ttl": "300s"
        }
    )
    
    # Check response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert data["key"] == "direct_event"
    assert dest_id in data["destinations"]

def test_throw_and_clear_event(test_client, clean_events_state):
    """Test throwing an event and clearing it."""
    dest_id = "test_dest"
    
    # Throw an event using the unified endpoint
    test_client.post(
        "/api/schedulers/events/throw",
        json={
            "event": "test_event",
            "scope": "dest",
            "destination": dest_id,
            "ttl": "300s"
        }
    )
    
    # Verify it was stored
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    data = json.loads(response.data)
    assert len(data["queue"]) == 1
    
    # Clear the event
    response = test_client.delete(f"/api/schedulers/events?destination={dest_id}")
    
    # Check clear response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "cleared" in data
    assert data["cleared"] == 1
    
    # Verify it was cleared
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    data = json.loads(response.data)
    assert len(data["queue"]) == 0

def test_global_event_api(test_client, clean_events_state, monkeypatch):
    """Test throwing a global event."""
    # Mock all_destinations
    def mock_all_destinations():
        return ["dest1", "dest2", "dest3"]
    
    monkeypatch.setattr("routes.scheduler_utils.all_destinations", mock_all_destinations)
    
    # Mock determine_scope_type to ensure "global" is recognized
    def mock_determine_scope_type(scope):
        if scope == "global":
            return "global", None, None
        else:
            return "dest", scope, None
    
    monkeypatch.setattr("routes.scheduler_utils.determine_scope_type", mock_determine_scope_type)
    
    # Throw a global event
    response = test_client.post(
        "/api/schedulers/events/throw",
        json={
            "event": "global_event",
            "scope": "global",
            "ttl": "60s"
        }
    )
    
    # Check response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert len(data["destinations"]) == 3
    assert "dest1" in data["destinations"]
    assert "dest2" in data["destinations"]
    assert "dest3" in data["destinations"]
    
    # Verify events were created for each destination
    assert "dest1" in active_events
    assert "global_event" in active_events["dest1"]
    assert "dest2" in active_events
    assert "global_event" in active_events["dest2"]
    assert "dest3" in active_events
    assert "global_event" in active_events["dest3"]

def test_group_event_api(test_client, clean_events_state, monkeypatch):
    """Test throwing a group event."""
    # Mock group_destinations
    def mock_get_destinations(group_id):
        if group_id == "test_group":
            return ["dest1", "dest2"]
        return []
    
    monkeypatch.setattr("routes.scheduler_utils.get_destinations_for_group", mock_get_destinations)
    
    # Mock determine_scope_type
    def mock_determine_scope_type(scope):
        if scope == "global":
            return "global", None, None
        elif scope == "test_group":
            return "group", None, "test_group"
        else:
            return "dest", scope, None
    
    monkeypatch.setattr("routes.scheduler_utils.determine_scope_type", mock_determine_scope_type)
    
    # Throw a group event 
    response = test_client.post(
        "/api/schedulers/events/throw",
        json={
            "event": "group_event",
            "scope": "group",
            "group": "test_group",
            "ttl": "60s"
        }
    )
    
    # Check response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert data["group"] == "test_group"
    
    # Verify events were created for each destination in the group
    assert "dest1" in active_events
    assert "group_event" in active_events["dest1"]
    assert "dest2" in active_events
    assert "group_event" in active_events["dest2"] 