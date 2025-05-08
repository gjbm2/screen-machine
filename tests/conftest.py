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

# -----------------------------------------------------------------------------
# Pytest global configuration: redirect ALL persistence paths to temp dirs
# -----------------------------------------------------------------------------

def pytest_configure(config):
    """Early-phase hook that runs once at collection time.
    It patches *scheduler_utils* so that **no test can touch production files**,
    even if a particular test module defines its own fixtures or calls the
    code before local fixtures run."""
    from routes import scheduler_utils as _sutils
    
    temp_root = tempfile.mkdtemp(prefix="scheduler_tests_")
    vars_path = os.path.join(temp_root, "_vars.json")
    storage_dir = os.path.join(temp_root, "state")
    os.makedirs(storage_dir, exist_ok=True)

    # Patch the registry path
    _sutils.VARS_REGISTRY_PATH = vars_path

    # Patch the storage path function
    def _mock_get_storage_path(dest_id: str) -> str:
        return os.path.join(storage_dir, f"{dest_id}.json")
    _sutils.get_scheduler_storage_path = _mock_get_storage_path

@pytest.fixture
def temp_registry_file(clean_scheduler_state):
    """Get the temporary registry file path from clean_scheduler_state.
    This ensures all tests use the same isolated registry path."""
    return clean_scheduler_state["registry_path"]

@pytest.fixture
def mock_scheduler_context():
    """Create a mock scheduler context."""
    return {
        "vars": {},
        "last_generated": None,
        "publish_destination": "test_dest"
    }

@pytest.fixture
def clean_scheduler_state(monkeypatch, tmp_path):
    """Provide clean scheduler state dictionaries and redirect all storage to temp paths."""
    # Create temp dirs for test storage
    test_storage_dir = tmp_path / "scheduler_storage"
    test_storage_dir.mkdir(exist_ok=True)
    test_registry_dir = tmp_path / "vars_registry"
    test_registry_dir.mkdir(exist_ok=True)
    
    # Mock in-memory state
    mock_contexts = {}
    mock_schedules = {}
    mock_states = {}
    mock_logs = {}
    
    # Path to the test registry file
    test_registry_path = str(test_registry_dir / "_vars.json")
    
    # Mock global variable references in scheduler_utils
    monkeypatch.setattr('routes.scheduler_utils.scheduler_contexts_stacks', mock_contexts)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_schedule_stacks', mock_schedules)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_states', mock_states)
    monkeypatch.setattr('routes.scheduler_utils.scheduler_logs', mock_logs)
    
    # CRITICAL: Also patch the same variables in the scheduler module to ensure they point to the same objects
    import routes.scheduler as _sched
    monkeypatch.setattr(_sched, 'scheduler_contexts_stacks', mock_contexts, raising=False)
    monkeypatch.setattr(_sched, 'scheduler_schedule_stacks', mock_schedules, raising=False)
    monkeypatch.setattr(_sched, 'scheduler_states', mock_states, raising=False)
    monkeypatch.setattr(_sched, 'scheduler_logs', mock_logs, raising=False)
    
    # CRITICAL: Also patch the scheduler_api module that also imports these globals
    import routes.scheduler_api as _sched_api
    monkeypatch.setattr(_sched_api, 'scheduler_contexts_stacks', mock_contexts, raising=False)
    monkeypatch.setattr(_sched_api, 'scheduler_schedule_stacks', mock_schedules, raising=False)
    monkeypatch.setattr(_sched_api, 'scheduler_states', mock_states, raising=False)
    monkeypatch.setattr(_sched_api, 'scheduler_logs', mock_logs, raising=False)
    monkeypatch.setattr(_sched_api, 'running_schedulers', {}, raising=False)
    monkeypatch.setattr(_sched_api, 'important_triggers', {}, raising=False)
    monkeypatch.setattr(_sched_api, 'active_events', {}, raising=False)
    
    # CRITICAL: Redirect the registry path to test directory
    monkeypatch.setattr('routes.scheduler_utils.VARS_REGISTRY_PATH', test_registry_path)
    
    # CRITICAL: Redirect the scheduler storage path function to use test directory
    def mock_get_storage_path(publish_destination: str) -> str:
        return str(test_storage_dir / f"{publish_destination}.json")
    
    monkeypatch.setattr('routes.scheduler_utils.get_scheduler_storage_path', mock_get_storage_path)
    
    # Return the clean state objects for test use
    return {
        "contexts": mock_contexts,
        "schedules": mock_schedules,
        "states": mock_states,
        "logs": mock_logs,
        "storage_dir": test_storage_dir,
        "registry_path": test_registry_path
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
def mock_scheduler_storage_path(clean_scheduler_state):
    """Get the temporary scheduler storage path from clean_scheduler_state.
    This ensures all tests use the same isolated storage path."""
    return clean_scheduler_state["storage_dir"]

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
def app(clean_scheduler_state):
    """Create and configure a Flask app for testing."""
    # Create a minimal Flask app for testing
    app = Flask(__name__)
    app.config['TESTING'] = True
    
    # Set the scheduler directory to our test storage directory
    app.config['SCHEDULER_DIR'] = str(clean_scheduler_state["storage_dir"])
    
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