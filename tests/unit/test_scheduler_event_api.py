import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from flask import Flask
from collections import deque
from routes.scheduler_api import scheduler_bp
from routes.scheduler_utils import EventEntry, active_events, event_history, throw_event
import uuid

@pytest.fixture
def test_client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(scheduler_bp)
    return app.test_client()

@pytest.fixture(autouse=True)
def clear_event_state():
    # Clear before test
    active_events.clear()
    event_history.clear()
    
    yield
    
    # Clear after test
    active_events.clear()
    event_history.clear()

def test_throw_event_endpoint(test_client):
    """Test the throw event API endpoint."""
    # Test event data
    event_data = {
        "event": "test_event",
        "scope": "test_dest",
        "ttl": "60s",
        "display_name": "Test Event",
        "payload": {"test": "data"}
    }
    
    # Post to the throw event endpoint
    response = test_client.post(
        "/api/schedulers/events/throw",
        json=event_data
    )
    
    # Verify response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert "test_dest" in data["destinations"]
    
    # Verify event was stored
    assert "test_dest" in active_events
    assert "test_event" in active_events["test_dest"]
    assert len(active_events["test_dest"]["test_event"]) == 1
    
    event_entry = active_events["test_dest"]["test_event"][0]
    assert event_entry.display_name == "Test Event"
    assert event_entry.payload == {"test": "data"}
    
    # Verify history
    assert "test_dest" in event_history
    assert len(event_history["test_dest"]) == 1
    assert event_history["test_dest"][0].key == "test_event"

def test_throw_event_group_scope(test_client):
    """Test throwing an event with group scope."""
    # Mock the group destinations
    with patch('routes.scheduler_utils.get_destinations_for_group') as mock_get_dests:
        mock_get_dests.return_value = ["dest1", "dest2", "dest3"]
        
        # Test event data
        event_data = {
            "event": "group_event",
            "scope": "test_group",
            "ttl": "60s"
        }
        
        # Post to the throw event endpoint
        response = test_client.post(
            "/api/schedulers/events/throw",
            json=event_data
        )
        
        # Verify response
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "queued"
        assert "test_group" in data
        assert data["group"] == "test_group"
        
        # Verify destinations are included
        assert len(data["destinations"]) == 3
        for dest in ["dest1", "dest2", "dest3"]:
            assert dest in data["destinations"]
            assert dest in active_events
            assert "group_event" in active_events[dest]
            assert len(active_events[dest]["group_event"]) == 1
            
            # Verify history
            assert dest in event_history
            assert len(event_history[dest]) == 1
            assert event_history[dest][0].key == "group_event"

def test_throw_event_global_scope(test_client):
    """Test throwing an event with global scope."""
    # Mock all destinations
    with patch('routes.scheduler_utils.all_destinations') as mock_all_dests:
        mock_all_dests.return_value = ["dest1", "dest2", "dest3"]
        
        # Test event data
        event_data = {
            "event": "global_event",
            "scope": "global",
            "ttl": "60s"
        }
        
        # Post to the throw event endpoint
        response = test_client.post(
            "/api/schedulers/events/throw",
            json=event_data
        )
        
        # Verify response
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "queued"
        
        # Verify destinations are included
        assert len(data["destinations"]) == 3
        for dest in ["dest1", "dest2", "dest3"]:
            assert dest in data["destinations"]

def test_throw_event_with_delay(test_client):
    """Test throwing an event with delay."""
    # Test event data
    event_data = {
        "event": "delayed_event",
        "scope": "test_dest",
        "ttl": "60s",
        "delay": "10s"
    }
    
    # Post to the throw event endpoint
    response = test_client.post(
        "/api/schedulers/events/throw",
        json=event_data
    )
    
    # Verify response
    assert response.status_code == 200
    
    # Verify event was stored with future activation time
    now = datetime.now(timezone.utc)
    event_entry = active_events["test_dest"]["delayed_event"][0]
    assert event_entry.active_from > now
    assert event_entry.active_from < now + timedelta(seconds=15)  # Allow a bit of tolerance
    
    # Verify history
    assert "test_dest" in event_history
    assert len(event_history["test_dest"]) == 1
    assert event_history["test_dest"][0].key == "delayed_event"

def test_throw_event_with_future_time(test_client):
    """Test throwing an event with future time."""
    # Test event data
    future_time = datetime.now(timezone.utc).isoformat()
    event_data = {
        "event": "future_event",
        "scope": "test_dest",
        "ttl": "60s",
        "future_time": future_time
    }
    
    # Post to the throw event endpoint
    response = test_client.post(
        "/api/schedulers/events/throw",
        json=event_data
    )
    
    # Verify response
    assert response.status_code == 200
    
    # Verify event was stored with future activation time
    event_entry = active_events["test_dest"]["future_event"][0]
    assert abs((event_entry.active_from - datetime.fromisoformat(future_time)).total_seconds()) < 1
    
    # Verify history
    assert "test_dest" in event_history
    assert len(event_history["test_dest"]) == 1
    assert event_history["test_dest"][0].key == "future_event"

def test_throw_event_missing_required_fields(test_client):
    """Test the throw event API endpoint with missing fields."""
    # Missing event field
    event_data = {
        "scope": "test_dest",
        "ttl": "60s"
    }
    
    # Post to the throw event endpoint
    response = test_client.post(
        "/api/schedulers/events/throw",
        json=event_data
    )
    
    # Verify response
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data

def test_get_events(test_client):
    """Test getting events for a destination."""
    dest_id = "test_dest"
    
    # Add some events
    with patch('routes.scheduler_utils.throw_event') as mock_throw:
        # Mock throw_event to update active_events directly
        def side_effect(*args, **kwargs):
            key = kwargs.get('key')
            dest = kwargs.get('dest_id')
            
            if dest not in active_events:
                active_events[dest] = {}
            if key not in active_events[dest]:
                active_events[dest][key] = deque()
                
            now = datetime.now(timezone.utc)
            entry = EventEntry(
                key=key,
                active_from=now,
                expires=now + timedelta(seconds=60),
                display_name=kwargs.get('display_name'),
                payload=kwargs.get('payload'),
                single_consumer=kwargs.get('single_consumer', False),
                created_at=now,
                unique_id=str(uuid.uuid4())
            )
            
            active_events[dest][key].append(entry)
            
            if dest not in event_history:
                event_history[dest] = []
            event_history[dest].append(entry)
            
            return {"status": "queued", "key": key, "destinations": [dest]}
        
        mock_throw.side_effect = side_effect
        
        # Add events via the API
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event1", "scope": "test_dest"}
        )
        
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event2", "scope": "test_dest"}
        )
    
    # Get events
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    
    # Verify response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "queue" in data
    assert "history" in data
    
    # Verify queue contents
    assert len(data["queue"]) == 2
    assert len(data["history"]) == 2

def test_clear_events(test_client):
    """Test clearing events for a destination."""
    dest_id = "test_dest"
    
    # Add some events first
    with patch('routes.scheduler_utils.throw_event') as mock_throw:
        # Mock throw_event to update active_events directly
        def side_effect(*args, **kwargs):
            key = kwargs.get('key')
            dest = kwargs.get('dest_id')
            
            if dest not in active_events:
                active_events[dest] = {}
            if key not in active_events[dest]:
                active_events[dest][key] = deque()
                
            now = datetime.now(timezone.utc)
            entry = EventEntry(
                key=key,
                active_from=now,
                expires=now + timedelta(seconds=60),
                display_name=kwargs.get('display_name'),
                payload=kwargs.get('payload'),
                single_consumer=kwargs.get('single_consumer', False),
                created_at=now,
                unique_id=str(uuid.uuid4())
            )
            
            active_events[dest][key].append(entry)
            
            if dest not in event_history:
                event_history[dest] = []
            event_history[dest].append(entry)
            
            return {"status": "queued", "key": key, "destinations": [dest]}
        
        mock_throw.side_effect = side_effect
        
        # Add events via the API
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event1", "scope": "test_dest"}
        )
        
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event2", "scope": "test_dest"}
        )
    
    # Clear events
    response = test_client.delete(f"/api/schedulers/events?destination={dest_id}")
    
    # Verify response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "cleared_active" in data
    assert data["cleared_active"] == 2
    
    # Verify events were cleared
    assert dest_id not in active_events or not any(active_events[dest_id].values()) 