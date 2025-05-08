import pytest
import os
import json
from datetime import datetime
from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_schedule_stacks, scheduler_states, scheduler_logs
from routes.scheduler_utils import VARS_REGISTRY_PATH
from flask import Flask, g
from flask.testing import FlaskClient
import tempfile
from unittest.mock import patch

@pytest.fixture
def temp_registry_file(tmp_path, monkeypatch):
    """Create a temporary registry file path and patch the global reference."""
    mock_registry_path = str(tmp_path / "_vars.json")
    # Make sure the actual registry file isn't modified during tests
    monkeypatch.setattr('routes.scheduler_utils.VARS_REGISTRY_PATH', mock_registry_path)
    return mock_registry_path

@pytest.fixture
def mock_scheduler_context():
    """Create a mock scheduler context."""
    return {
        "vars": {},
        "last_generated": None,
        "publish_destination": "test_dest"
    }

@pytest.fixture
def clean_scheduler_state(monkeypatch):
    """Provide clean scheduler state dictionaries."""
    mock_contexts = {}
    mock_schedules = {}
    mock_states = {}
    mock_logs = {}
    
    monkeypatch.setattr('routes.scheduler_utils.scheduler_contexts_stacks', mock_contexts)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_schedule_stacks', mock_schedules)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_states', mock_states)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_logs', mock_logs)
    
    return {
        "contexts": mock_contexts,
        "schedules": mock_schedules,
        "states": mock_states,
        "logs": mock_logs
    }

@pytest.fixture
def setup_test_destination(clean_scheduler_state):
    """Setup a test destination with a basic context."""
    dest_id = "test_dest"
    
    # Setup empty context stack
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {},
        "last_generated": None,
        "publish_destination": dest_id
    }]
    
    # Setup empty schedule stack
    clean_scheduler_state["schedules"][dest_id] = []
    
    # Set state to stopped
    clean_scheduler_state["states"][dest_id] = "stopped"
    
    # Setup empty logs
    clean_scheduler_state["logs"][dest_id] = []
    
    # Add to global stacks to ensure they exist
    from routes.scheduler_utils import scheduler_contexts_stacks, scheduler_schedule_stacks, scheduler_states
    
    scheduler_contexts_stacks[dest_id] = clean_scheduler_state["contexts"][dest_id]
    scheduler_schedule_stacks[dest_id] = clean_scheduler_state["schedules"][dest_id]
    scheduler_states[dest_id] = clean_scheduler_state["states"][dest_id]
    
    return dest_id

@pytest.fixture
def current_time():
    """Return the current time."""
    return datetime.now()

@pytest.fixture
def mock_now():
    """Return the current time (alternative name)."""
    return datetime.now()

@pytest.fixture
def output_list():
    """Provide an empty output list for logging."""
    return []

@pytest.fixture
def mock_scheduler_storage_path(tmp_path, monkeypatch):
    """Mock the scheduler storage path to use a temp directory."""
    def mock_path(dest_id):
        return str(tmp_path / f"{dest_id}.json")
    
    monkeypatch.setattr('routes.scheduler_utils.get_scheduler_storage_path', mock_path)
    return tmp_path

@pytest.fixture
def enable_testing_mode(monkeypatch):
    """Set the TESTING environment variable to use mock services."""
    # Save previous value
    previous_value = os.environ.get('TESTING')
    # Set testing mode
    os.environ['TESTING'] = 'true'
    
    yield
    
    # Restore previous value or remove if it wasn't set
    if previous_value is not None:
        os.environ['TESTING'] = previous_value
    else:
        os.environ.pop('TESTING', None)

@pytest.fixture
def test_schedule_basic():
    """Create a basic test schedule with just initial_actions."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "test_var", "input": {"value": "initial_value"}}
            ]
        }
    }

@pytest.fixture
def test_schedule_with_final():
    """Create a test schedule with initial and final actions."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initial_var", "input": {"value": "initial_value"}}
            ]
        },
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "final_var", "input": {"value": "final_value"}}
            ]
        }
    }

@pytest.fixture
def test_schedule_with_trigger():
    """Create a test schedule with a day_of_week trigger."""
    today = datetime.now().strftime("%A")  # Current day of week
    current_time = datetime.now().strftime("%H:%M")  # Current time
    
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initial_var", "input": {"value": "initial_value"}}
            ]
        },
        "triggers": [
            {
                "type": "day_of_week",
                "days": [today],
                "scheduled_actions": [
                    {
                        "time": current_time,
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "trigger_var", "input": {"value": "trigger_value"}}
                            ]
                        }
                    }
                ]
            }
        ],
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "final_var", "input": {"value": "final_value"}}
            ]
        }
    }

@pytest.fixture
def test_schedule_with_event():
    """Create a test schedule with an event trigger."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initial_var", "input": {"value": "initial_value"}}
            ]
        },
        "triggers": [
            {
                "type": "event",
                "value": "TestEvent",
                "trigger_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "event_var", "input": {"value": "event_value"}}
                    ]
                }
            }
        ],
        "final_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "final_var", "input": {"value": "final_value"}}
            ]
        }
    }

@pytest.fixture
def test_schedule_generate_animate():
    """Create a test schedule that uses generate and animate instructions."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "prompt", "input": {"value": "test prompt for generation"}},
                {
                    "action": "generate",
                    "input": {
                        "prompt": "test prompt for generation"
                    },
                    "refiner": "test_refiner",
                    "workflow": "text-to-image",
                    "history_var": "generation_history"
                },
                {
                    "action": "animate",
                    "input": {
                        "prompt": "animate the generated image"
                    },
                    "refiner": "animator"
                }
            ]
        }
    }

@pytest.fixture
def app():
    """Create and configure a Flask app for testing."""
    # Create a minimal Flask app for testing
    app = Flask(__name__)
    app.config['TESTING'] = True
    
    # Setup temp directory for file storage during tests
    with tempfile.TemporaryDirectory() as temp_dir:
        app.config['SCHEDULER_DIR'] = temp_dir
        
        # Use patch to make sure get_scheduler_storage_path uses the test directory
        with patch('routes.scheduler_utils.get_scheduler_storage_path', 
                  lambda dest: os.path.join(temp_dir, f"{dest}.json")):
            yield app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

@pytest.fixture
def app_context(app):
    """Provide an application context for tests that need it."""
    with app.app_context():
        yield

@pytest.fixture
def request_context(app):
    """Provide a request context for tests that need it."""
    with app.test_request_context():
        yield

@pytest.fixture
def app_request_context(app):
    """Provide both application and request context for tests that need it."""
    with app.app_context():
        with app.test_request_context():
            yield

@pytest.fixture
def clean_scheduler_state():
    """Clean up scheduler state before and after tests."""
    # Store original state
    original_states = dict(scheduler_states)
    original_contexts = {k: v[:] for k, v in scheduler_contexts_stacks.items()}
    original_schedules = {k: v[:] for k, v in scheduler_schedule_stacks.items()}
    original_logs = {k: v[:] for k, v in scheduler_logs.items()}
    
    # Clear state for test
    scheduler_states.clear()
    scheduler_contexts_stacks.clear()
    scheduler_schedule_stacks.clear()
    scheduler_logs.clear()
    
    yield {
        "states": scheduler_states,
        "contexts": scheduler_contexts_stacks,
        "schedules": scheduler_schedule_stacks,
        "logs": scheduler_logs
    }
    
    # Restore original state
    scheduler_states.clear()
    scheduler_states.update(original_states)
    
    scheduler_contexts_stacks.clear()
    scheduler_contexts_stacks.update(original_contexts)
    
    scheduler_schedule_stacks.clear()
    scheduler_schedule_stacks.update(original_schedules)
    
    scheduler_logs.clear()
    scheduler_logs.update(original_logs)

@pytest.fixture
def test_schedule():
    """Create a test schedule with the correct schema."""
    return {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "initialized", "input": {"value": True}}
            ]
        },
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "scheduled_actions": [
                    {
                        "time": "12:00",
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "lunch_time", "input": {"value": True}}
                            ]
                        }
                    }
                ]
            }
        ]
    } 