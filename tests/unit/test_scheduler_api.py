import pytest
from datetime import datetime
import json
from unittest.mock import patch, MagicMock
from flask import Flask
from routes.scheduler_api import (
    scheduler_bp,
    scheduler_schedule_stacks,
    scheduler_states,
    scheduler_logs,
    scheduler_contexts_stacks,
    running_schedulers
)

# Test fixtures
@pytest.fixture
def basic_schedule():
    return {
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "scheduled_actions": [
                    {
                        "time": "08:00",
                        "repeat_schedule": {
                            "every": "1",
                            "until": "23:00"
                        },
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "test", "input": {"value": "test"}}
                            ]
                        }
                    }
                ]
            }
        ],
        "initial_actions": {
            "instructions_block": []
        },
        "final_actions": {
            "instructions_block": []
        }
    }

@pytest.fixture
def test_client():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(scheduler_bp)
    return app.test_client()

@pytest.fixture(autouse=True)
def clear_scheduler_state():
    # Clear before test
    scheduler_schedule_stacks.clear()
    scheduler_states.clear()
    scheduler_logs.clear()
    scheduler_contexts_stacks.clear()
    running_schedulers.clear()
    
    # Initialize scheduler logs for test_dest
    scheduler_logs["test_dest"] = []
    scheduler_logs["test_dest1"] = []
    scheduler_logs["test_dest2"] = []
    
    yield
    
    # Clear after test
    scheduler_schedule_stacks.clear()
    scheduler_states.clear()
    scheduler_logs.clear()
    scheduler_contexts_stacks.clear()
    running_schedulers.clear()

def test_start_scheduler(test_client, basic_schedule):
    """Test starting a scheduler with a valid schedule."""
    response = test_client.post(
        "/api/schedulers/test_dest",
        json=basic_schedule
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "started"
    assert data["destination"] == "test_dest"
    assert "test_dest" in running_schedulers

def test_start_scheduler_invalid_schedule(test_client):
    """Test starting a scheduler with an invalid schedule."""
    invalid_schedule = {"invalid": "schedule"}
    response = test_client.post(
        "/api/schedulers/test_dest",
        json=invalid_schedule
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data

def test_stop_scheduler(test_client, basic_schedule):
    """Test stopping a running scheduler."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Then stop it
    response = test_client.delete("/api/schedulers/test_dest")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "stopped"
    assert data["destination"] == "test_dest"
    assert data["context_reset"] is True
    assert "test_dest" not in running_schedulers

def test_get_scheduler_schedule(test_client, basic_schedule):
    """Test getting the current schedule for a scheduler."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Then get its schedule
    response = test_client.get("/api/schedulers/test_dest/schedule")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["destination"] == "test_dest"
    assert data["schedule"] == basic_schedule
    assert data["stack_size"] == 1

def test_pause_unpause_scheduler(test_client, basic_schedule):
    """Test pausing and unpausing a scheduler."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Initialize scheduler state
    scheduler_states["test_dest"] = "running"
    
    # Pause it
    response = test_client.post("/api/schedulers/test_dest/pause")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "paused"
    assert scheduler_states["test_dest"] == "paused"
    
    # Unpause it
    response = test_client.post("/api/schedulers/test_dest/unpause")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "running"
    assert scheduler_states["test_dest"] == "running"

def test_get_scheduler_status(test_client, basic_schedule):
    """Test getting the status of a scheduler."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Initialize scheduler state
    scheduler_states["test_dest"] = "running"
    
    # Get its status
    response = test_client.get("/api/schedulers/test_dest/status")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "running"
    assert data["destination"] == "test_dest"
    assert "is_running" in data
    assert data["is_running"] is True

def test_get_scheduler_context(test_client, basic_schedule):
    """Test getting and setting scheduler context."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Initialize context
    scheduler_contexts_stacks["test_dest"] = [{
        "vars": {},
        "publish_destination": "test_dest"
    }]
    
    # Set context variable
    response = test_client.post(
        "/api/schedulers/test_dest/context",
        json={
            "var_name": "test_var",
            "var_value": "test_value"
        }
    )
    assert response.status_code == 200
    
    # Get context
    response = test_client.get("/api/schedulers/test_dest/context")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "vars" in data
    assert data["vars"].get("test_var") == "test_value"

def test_trigger_event(test_client, basic_schedule):
    """Test triggering an event for a scheduler."""
    # First start a scheduler
    test_client.post("/api/schedulers/test_dest", json=basic_schedule)
    
    # Trigger an event
    event_data = {
        "event": "user-started-generation",
        "scope": "dest", 
        "destination": "test_dest"
    }
    response = test_client.post(
        "/api/schedulers/events/throw",
        json=event_data
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "queued"
    assert data["key"] == "user-started-generation"
    assert "test_dest" in data["destinations"]

def test_get_all_scheduler_statuses(test_client, basic_schedule):
    """Test getting statuses of all schedulers."""
    # Start multiple schedulers
    test_client.post("/api/schedulers/test_dest1", json=basic_schedule)
    test_client.post("/api/schedulers/test_dest2", json=basic_schedule)
    
    # Initialize states
    scheduler_states["test_dest1"] = "running"
    scheduler_states["test_dest2"] = "running"
    
    # Get all statuses
    response = test_client.get("/api/schedulers/all/status")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "statuses" in data
    statuses = data["statuses"]
    assert "test_dest1" in statuses
    assert "test_dest2" in statuses
    assert statuses["test_dest1"]["status"] == "running"
    assert statuses["test_dest2"]["status"] == "running" 