import pytest
import json
from datetime import datetime
from unittest.mock import patch, MagicMock
from flask import Flask
from routes.scheduler_api import (
    scheduler_bp,
    active_events
)
from routes.scheduler_utils import event_history

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
        "scope": "dest",
        "destination": "test_dest",
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

def test_throw_event_group_scope(test_client):
    """Test throwing an event with group scope."""
    # Mock the group destinations
    with patch('routes.scheduler_utils.get_destinations_for_group') as mock_get_dests:
        mock_get_dests.return_value = ["dest1", "dest2", "dest3"]
        
        # Test event data
        event_data = {
            "event": "group_event",
            "scope": "group",
            "group": "test_group",
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
        assert "group" in data
        assert data["group"] == "test_group"
        
        # Verify destinations are included
        assert len(data["destinations"]) == 3
        for dest in ["dest1", "dest2", "dest3"]:
            assert dest in data["destinations"]

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
        "scope": "dest",
        "destination": "test_dest",
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
    now = datetime.utcnow()
    event_entry = active_events["test_dest"]["delayed_event"][0]
    assert event_entry.active_from > now

def test_throw_event_with_future_time(test_client):
    """Test throwing an event with future time."""
    # Test event data
    future_time = (datetime.utcnow()).isoformat()
    event_data = {
        "event": "future_event",
        "scope": "dest",
        "destination": "test_dest",
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

def test_throw_event_missing_required_fields(test_client):
    """Test the throw event API endpoint with missing fields."""
    # Missing event field
    event_data = {
        "scope": "dest",
        "destination": "test_dest"
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
                active_events[dest][key] = []
                
            entry = MagicMock()
            entry.key = key
            entry.active_from = datetime.utcnow()
            entry.expires = datetime.utcnow()
            entry.display_name = kwargs.get('display_name')
            entry.payload = kwargs.get('payload')
            entry.single_consumer = kwargs.get('single_consumer', False)
            entry.created_at = datetime.utcnow()
            
            active_events[dest][key].append(entry)
            
            if dest not in event_history:
                event_history[dest] = []
            event_history[dest].append(entry)
            
            return {"status": "queued", "key": key, "destinations": [dest]}
        
        mock_throw.side_effect = side_effect
        
        # Add events via the API
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event1", "scope": "dest", "destination": dest_id}
        )
        
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event2", "scope": "dest", "destination": dest_id}
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

def test_clear_events(test_client):
    """Test clearing events for a destination."""
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
                active_events[dest][key] = []
                
            entry = MagicMock()
            entry.key = key
            entry.active_from = datetime.utcnow()
            entry.expires = datetime.utcnow()
            entry.display_name = kwargs.get('display_name')
            entry.payload = kwargs.get('payload')
            entry.single_consumer = kwargs.get('single_consumer', False)
            entry.created_at = datetime.utcnow()
            
            active_events[dest][key].append(entry)
            
            if dest not in event_history:
                event_history[dest] = []
            event_history[dest].append(entry)
            
            return {"status": "queued", "key": key, "destinations": [dest]}
        
        mock_throw.side_effect = side_effect
        
        # Add events via the API
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event1", "scope": "dest", "destination": dest_id}
        )
        
        test_client.post(
            "/api/schedulers/events/throw",
            json={"event": "event2", "scope": "dest", "destination": dest_id}
        )
    
    # Clear a specific event
    response = test_client.delete(f"/api/schedulers/events/event1?destination={dest_id}")
    
    # Verify response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["cleared"] >= 0
    
    # Get events to verify
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    data = json.loads(response.data)
    
    # Verify event1 is gone but event2 remains
    event1_exists = any(e["key"] == "event1" for e in data["queue"])
    event2_exists = any(e["key"] == "event2" for e in data["queue"])
    
    assert not event1_exists
    assert event2_exists
    
    # Clear all events
    response = test_client.delete(f"/api/schedulers/events?destination={dest_id}")
    
    # Verify response
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["cleared"] >= 0
    
    # Get events to verify
    response = test_client.get(f"/api/schedulers/events?destination={dest_id}")
    data = json.loads(response.data)
    
    # Verify all events are gone
    assert len(data["queue"]) == 0 