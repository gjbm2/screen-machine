import pytest
import json
import os
from datetime import datetime
from unittest.mock import patch, MagicMock, mock_open

from routes.scheduler_utils import (
    update_scheduler_state, scheduler_contexts_stacks, scheduler_schedule_stacks,
    save_scheduler_state, load_scheduler_state,
    get_current_context, push_context
)

from routes.scheduler_api import (
    api_load_schedule, api_get_scheduler_schedule, api_unload_schedule
)

@pytest.fixture
def basic_schedule():
    """Return a basic valid schedule for testing."""
    return {
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "scheduled_actions": [
                    {
                        "time": "09:00",
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "day_start", "input": {"value": "work day started"}}
                            ]
                        }
                    }
                ]
            }
        ],
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "schedule_loaded", "input": {"value": "true"}}
            ]
        }
    }

@pytest.fixture
def another_schedule():
    """Return another valid schedule for testing."""
    return {
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Saturday", "Sunday"],
                "scheduled_actions": [
                    {
                        "time": "10:00",
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "weekend", "input": {"value": "weekend started"}}
                            ]
                        }
                    }
                ]
            }
        ],
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "weekend_schedule", "input": {"value": "true"}}
            ]
        }
    }

@pytest.fixture
def multi_destination_setup(clean_scheduler_state):
    """Set up multiple publish destinations."""
    destinations = ["cloud", "devtest", "local"]
    
    # Initialize each destination with empty stacks
    for dest in destinations:
        # Clear any existing state
        if dest in scheduler_contexts_stacks:
            scheduler_contexts_stacks[dest] = []
        
        if dest in scheduler_schedule_stacks:
            scheduler_schedule_stacks[dest] = []
        
        # Create a basic context
        scheduler_contexts_stacks[dest] = [{
            "vars": {},
            "publish_destination": dest
        }]
        
        # Save initial state
        update_scheduler_state(
            dest,
            schedule_stack=[],
            context_stack=scheduler_contexts_stacks[dest]
        )
    
    return {"destinations": destinations}

@pytest.fixture
def mock_flask_request(app_request_context):
    """Mock Flask request context for API calls."""
    with patch('routes.scheduler_api.request') as mock_request:
        mock_request.json = {}
        mock_request.get_json = lambda: mock_request.json
        yield mock_request

def test_load_first_schedule(multi_destination_setup, basic_schedule, mock_flask_request, app_request_context):
    """Test loading a schedule when none exists."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Verify no schedule stack exists initially
    assert len(scheduler_schedule_stacks.get(dest, [])) == 0
    
    # Set up request with the basic schedule
    mock_flask_request.json = basic_schedule
    
    # Mock jsonschema validation
    with patch('jsonschema.validate', return_value=None):
        # Mock jsonify to return a response-like object with a json attribute
        with patch('routes.scheduler_api.jsonify') as mock_jsonify:
            mock_response = MagicMock()
            mock_response.json = {"status": "ok"}
            mock_jsonify.return_value = mock_response
            
            # Load the schedule
            with patch('routes.scheduler_api.request.get_json', return_value=basic_schedule):
                response = api_load_schedule(dest)
    
    # Verify response
    if isinstance(response, tuple):
        response = response[0]  # Extract first element if it's a tuple
    assert response.json["status"] == "ok"
    
    # Verify schedule stack has been created with our schedule
    assert dest in scheduler_schedule_stacks
    assert len(scheduler_schedule_stacks[dest]) == 1
    assert scheduler_schedule_stacks[dest][0] == basic_schedule
    
    # Verify state was saved
    with patch('routes.scheduler_utils.get_scheduler_storage_path') as mock_path:
        mock_path.return_value = f"/tmp/{dest}_state.json"
        
        # Mock open to simulate file reading without writing
        mock_file_content = json.dumps({
            "schedule_stack": scheduler_schedule_stacks[dest],
            "context_stack": scheduler_contexts_stacks[dest],
            "state": "stopped"
        })
        
        with patch("builtins.open", mock_open(read_data=mock_file_content)):
            state = load_scheduler_state(dest)
            
            # Verify schedule is in the state
            assert "schedule_stack" in state
            assert len(state["schedule_stack"]) == 1
            assert state["schedule_stack"][0] == basic_schedule

def test_load_schedule_preserves_context_vars(multi_destination_setup, basic_schedule, mock_flask_request):
    """Test loading a schedule preserves existing context variables."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Set some variables in the context
    context = get_current_context(dest)
    context["vars"]["existing_var"] = "existing_value"
    context["vars"]["another_var"] = "another_value"
    
    # Update the state with these variables
    update_scheduler_state(dest, context_stack=scheduler_contexts_stacks[dest])
    
    # Set up request with the basic schedule
    mock_flask_request.json = basic_schedule
    
    # Mock jsonschema validation
    with patch('jsonschema.validate', return_value=None):
        # Load the schedule
        with patch('routes.scheduler_api.request.get_json', return_value=basic_schedule):
            api_load_schedule(dest)
    
    # Verify context vars still exist
    context = get_current_context(dest)
    assert context["vars"]["existing_var"] == "existing_value"
    assert context["vars"]["another_var"] == "another_value"

def test_unload_schedule(multi_destination_setup, basic_schedule, mock_flask_request, app_request_context):
    """Test unloading a schedule."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # First load a schedule
    scheduler_schedule_stacks[dest] = [basic_schedule]
    update_scheduler_state(dest, schedule_stack=scheduler_schedule_stacks[dest])
    
    # Verify it's loaded
    assert len(scheduler_schedule_stacks[dest]) == 1
    
    # Mock jsonify for unloading
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_response = MagicMock()
        mock_response.json = {"status": "unloaded"}
        mock_jsonify.return_value = mock_response
        
        # Now unload it
        response = api_unload_schedule(dest)
    
    # Verify response
    if isinstance(response, tuple):
        response = response[0]  # Extract first element if it's a tuple
    assert response.json["status"] == "unloaded"

def test_load_multiple_schedules(multi_destination_setup, basic_schedule, another_schedule, mock_flask_request):
    """Test loading multiple schedules in sequence."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Load first schedule
    mock_flask_request.json = basic_schedule
    with patch('jsonschema.validate', return_value=None):
        with patch('routes.scheduler_api.request.get_json', return_value=basic_schedule):
            api_load_schedule(dest)
    
    # Verify first schedule is loaded
    assert len(scheduler_schedule_stacks[dest]) == 1
    assert scheduler_schedule_stacks[dest][0] == basic_schedule
    
    # Load second schedule
    mock_flask_request.json = another_schedule
    with patch('jsonschema.validate', return_value=None):
        with patch('routes.scheduler_api.request.get_json', return_value=another_schedule):
            api_load_schedule(dest)
    
    # Verify second schedule replaced the first
    assert len(scheduler_schedule_stacks[dest]) == 1
    assert scheduler_schedule_stacks[dest][0] == another_schedule

def test_get_schedule(multi_destination_setup, basic_schedule, app_request_context):
    """Test getting the current schedule."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # First load a schedule
    scheduler_schedule_stacks[dest] = [basic_schedule]
    update_scheduler_state(dest, schedule_stack=scheduler_schedule_stacks[dest])
    
    # Get the schedule
    response = api_get_scheduler_schedule(dest)
    
    # Verify response
    assert "schedule" in response.json
    assert response.json["schedule"] == basic_schedule

def test_load_schedule_doesnt_affect_other_destinations(multi_destination_setup, basic_schedule, another_schedule, mock_flask_request):
    """Test loading a schedule for one destination doesn't affect others."""
    destinations = multi_destination_setup["destinations"]
    
    # Load first schedule to first destination
    mock_flask_request.json = basic_schedule
    with patch('jsonschema.validate', return_value=None):
        with patch('routes.scheduler_api.request.get_json', return_value=basic_schedule):
            api_load_schedule(destinations[0])
    
    # Load second schedule to second destination
    mock_flask_request.json = another_schedule
    with patch('jsonschema.validate', return_value=None):
        with patch('routes.scheduler_api.request.get_json', return_value=another_schedule):
            api_load_schedule(destinations[1])
    
    # Verify each destination has its own schedule
    assert len(scheduler_schedule_stacks[destinations[0]]) == 1
    assert scheduler_schedule_stacks[destinations[0]][0] == basic_schedule
    
    assert len(scheduler_schedule_stacks[destinations[1]]) == 1
    assert scheduler_schedule_stacks[destinations[1]][0] == another_schedule
    
    # Verify third destination still has no schedule
    assert len(scheduler_schedule_stacks[destinations[2]]) == 0

def test_push_context_preserves_schedule(multi_destination_setup, basic_schedule):
    """Test that push_context preserves the schedule stack."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Load a schedule
    scheduler_schedule_stacks[dest] = [basic_schedule]
    update_scheduler_state(dest, schedule_stack=scheduler_schedule_stacks[dest])
    
    # Push a new context
    new_context = {
        "vars": {"new_var": "new_value"},
        "publish_destination": dest
    }
    push_context(dest, new_context)
    
    # Verify schedule is still there
    assert len(scheduler_schedule_stacks[dest]) == 1
    assert scheduler_schedule_stacks[dest][0] == basic_schedule

def test_load_malformed_schedule(multi_destination_setup, mock_flask_request, app_request_context):
    """Test loading a malformed schedule with validation errors."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Create a malformed schedule (missing required fields)
    malformed_schedule = {
        "triggers": [
            {
                "type": "invalid_type",
                "scheduled_actions": []
            }
        ]
    }
    
    # Set up request
    mock_flask_request.json = malformed_schedule
    
    # Mock jsonify to return error
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_response = MagicMock()
        mock_response.json = {"error": "Invalid schedule format"}
        mock_jsonify.return_value = mock_response
        
        # Mock jsonschema validation to fail
        validation_error = Exception("Invalid schedule format")
        with patch('jsonschema.validate', side_effect=validation_error):
            with patch('routes.scheduler_api.request.get_json', return_value=malformed_schedule):
                response = api_load_schedule(dest)
    
    # Verify response indicates error
    if isinstance(response, tuple):
        response = response[0]  # Extract first element if it's a tuple
    assert "error" in response.json
    assert "Invalid schedule format" in response.json["error"]

def test_empty_schedule(multi_destination_setup, mock_flask_request, app_request_context):
    """Test loading an empty schedule (just triggers with no actions)."""
    destinations = multi_destination_setup["destinations"]
    dest = destinations[0]
    
    # Create minimally valid but empty schedule
    empty_schedule = {
        "triggers": []
    }
    
    # Set up request
    mock_flask_request.json = empty_schedule
    
    # Mock jsonify to return success
    with patch('routes.scheduler_api.jsonify') as mock_jsonify:
        mock_response = MagicMock()
        mock_response.json = {"status": "ok"}
        mock_jsonify.return_value = mock_response
        
        # Mock jsonschema validation
        with patch('jsonschema.validate', return_value=None):
            with patch('routes.scheduler_api.request.get_json', return_value=empty_schedule):
                response = api_load_schedule(dest)
    
    # Verify response
    if isinstance(response, tuple):
        response = response[0]  # Extract first element if it's a tuple
    assert response.json["status"] == "ok" 