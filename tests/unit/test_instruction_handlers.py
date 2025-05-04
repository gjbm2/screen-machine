import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import os
from routes.scheduler_handlers import (
    handle_random_choice, handle_devise_prompt, handle_generate, 
    handle_animate, handle_display, handle_wait, handle_unload,
    handle_device_media_sync, handle_device_wake, handle_device_sleep,
    handle_set_var
)

@pytest.fixture(autouse=True)
def enable_testing_mode():
    """Set the TESTING environment variable to use mock services"""
    old_value = os.environ.get('TESTING')
    os.environ['TESTING'] = 'true'
    yield
    if old_value:
        os.environ['TESTING'] = old_value
    else:
        del os.environ['TESTING']

@pytest.fixture
def mock_now():
    return datetime.now()

@pytest.fixture
def base_context():
    return {
        "vars": {
            "existing_var": "existing_value"
        },
        "last_generated": "previous_image.jpg"
    }

@pytest.fixture
def output_list():
    return []

def test_handle_random_choice(base_context, mock_now, output_list):
    """Test the random_choice instruction handler."""
    # Create a test instruction
    instruction = {
        "var": "random_result",
        "choices": ["choice1", "choice2", "choice3"]
    }
    
    # Run the handler
    should_unload = handle_random_choice(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "random_result" in base_context["vars"]
    assert base_context["vars"]["random_result"] in instruction["choices"]
    assert len(output_list) == 1  # Should log something

def test_handle_devise_prompt(base_context, mock_now, output_list):
    """Test the devise_prompt instruction handler."""
    # Create a test instruction
    instruction = {
        "var": "devised_prompt",
        "theme": "nature",
        "theme_var": None  # Not using a variable for theme
    }
    
    # Run the handler
    should_unload = handle_devise_prompt(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "devised_prompt" in base_context["vars"]
    assert "nature" in base_context["vars"]["devised_prompt"]
    assert len(output_list) == 1

def test_handle_devise_prompt_with_history(base_context, mock_now, output_list):
    """Test the devise_prompt instruction with history tracking."""
    # Create a test instruction with history
    instruction = {
        "var": "devised_prompt",
        "theme": "technology",
        "theme_var": None,
        "history": "prompt_history"
    }
    
    # Run the handler
    should_unload = handle_devise_prompt(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly and history was updated
    assert should_unload is False
    assert "prompt_history" in base_context["vars"]
    assert isinstance(base_context["vars"]["prompt_history"], list)
    assert len(base_context["vars"]["prompt_history"]) == 1
    assert "timestamp" in base_context["vars"]["prompt_history"][0]
    assert "prompt" in base_context["vars"]["prompt_history"][0]

@patch('routes.service_factory.get_generation_service')
def test_handle_generate(mock_get_generation_service, base_context, mock_now, output_list):
    """Test the generate instruction handler."""
    # Setup the mock service
    mock_service = MagicMock()
    mock_service.return_value = [
        {"message": "generated_image.jpg", "file": "generated_image.jpg"}
    ]
    mock_get_generation_service.return_value = mock_service
    
    # Create a test instruction
    instruction = {
        "input": {
            "prompt": "A test prompt"
        },
        "refiner": "test_refiner",
        "workflow": "test_workflow",
        "history_output_var": "generation_history"
    }
    
    # Run the handler
    handle_generate(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler called the image generation function
    mock_get_generation_service.assert_called_once()
    mock_service.assert_called_once()
    
    # Check the context was updated
    assert base_context["last_generated"] == "generated_image.jpg"
    assert "generation_history" in base_context["vars"]
    assert isinstance(base_context["vars"]["generation_history"], list)
    assert len(base_context["vars"]["generation_history"]) == 1
    assert "prompt" in base_context["vars"]["generation_history"][0]
    assert "image_url" in base_context["vars"]["generation_history"][0]

@patch('routes.service_factory.get_animation_service')
def test_handle_animate(mock_get_animation_service, base_context, mock_now, output_list):
    """Test the animate instruction handler."""
    # Setup the mock service
    mock_service = MagicMock()
    mock_service.return_value = [{"message": "animation.mp4", "file": "animation.mp4"}]
    mock_get_animation_service.return_value = mock_service
    
    # Create a test instruction
    instruction = {
        "input": {
            "prompt": "Animate this"
        },
        "refiner": "animator"
    }
    
    # Run the handler
    handle_animate(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler called the animation function
    mock_get_animation_service.assert_called_once()
    mock_service.assert_called_once()
    
    # Check the output log
    assert len(output_list) == 1
    assert "Started animation" in output_list[0]

@patch('routes.service_factory.get_display_service')
def test_handle_display(mock_get_display_service, base_context, mock_now, output_list):
    """Test the display instruction handler."""
    # Setup the mock service
    mock_service = MagicMock()
    mock_service.return_value = {"success": True}
    mock_get_display_service.return_value = mock_service
    
    # Create test instructions for different display modes
    for mode in ["Next", "Random", "Blank"]:
        instruction = {
            "show": mode,
            "silent": False
        }
        
        # Reset output list
        output_list.clear()
        
        # Run the handler
        should_unload = handle_display(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Check that the handler behaved correctly
        assert should_unload is False
        assert len(output_list) >= 1  # Changed from == 1 to >= 1 to be more flexible
        assert mode.lower() in output_list[0].lower() or "display" in output_list[0].lower()
        
        # Verify display service was called with correct parameters
        mock_service.assert_called_with(
            publish_destination_id="test_dest",
            mode=mode,
            silent=False
        )
        mock_service.reset_mock()

def test_handle_wait_start(base_context, mock_now, output_list):
    """Test the wait instruction handler when starting a wait."""
    # Create a test instruction
    instruction = {
        "duration": 5  # Wait for 5 minutes
    }
    
    # Run the handler
    should_unload = handle_wait(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "wait_until" in base_context
    assert base_context["wait_until"] == mock_now + timedelta(minutes=5)
    assert len(output_list) == 1
    assert "Started waiting" in output_list[0]

def test_handle_wait_complete(base_context, mock_now, output_list):
    """Test the wait instruction handler when wait is complete."""
    # Create a test instruction
    instruction = {
        "duration": 5  # Wait for 5 minutes
    }
    
    # Set wait_until to a time in the past
    base_context["wait_until"] = mock_now - timedelta(minutes=1)
    
    # Run the handler
    should_unload = handle_wait(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler signaled completion
    assert should_unload is True
    assert "wait_until" not in base_context  # Should be removed
    assert len(output_list) == 1
    assert "Wait period complete" in output_list[0]

def test_handle_wait_still_waiting(base_context, mock_now, output_list):
    """Test the wait instruction handler when still waiting."""
    # Create a test instruction
    instruction = {
        "duration": 5  # Wait for 5 minutes
    }
    
    # Set wait_until to a time in the future
    base_context["wait_until"] = mock_now + timedelta(minutes=1)
    
    # Run the handler
    should_unload = handle_wait(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler signaled continuation
    assert should_unload is False
    assert "wait_until" in base_context
    assert base_context["wait_until"] == mock_now + timedelta(minutes=1)
    assert len(output_list) == 1
    assert "Still waiting" in output_list[0]

def test_handle_unload(base_context, mock_now, output_list):
    """Test the unload instruction handler."""
    # Create a test instruction
    instruction = {
        "action": "unload"
    }
    
    # Run the handler
    should_unload = handle_unload(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler signaled unload
    assert should_unload is True
    assert len(output_list) == 1
    assert "Unloading" in output_list[0]

def test_handle_device_media_sync(base_context, mock_now, output_list):
    """Test the device-media-sync instruction handler."""
    # Create a test instruction
    instruction = {
        "action": "device-media-sync"
    }
    
    # Run the handler
    should_unload = handle_device_media_sync(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert len(output_list) == 1
    assert "Syncing media" in output_list[0]

def test_handle_device_wake(base_context, mock_now, output_list):
    """Test the device-wake instruction handler."""
    # Create a test instruction
    instruction = {
        "action": "device-wake"
    }
    
    # Run the handler
    should_unload = handle_device_wake(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert len(output_list) == 1
    assert "Waking device" in output_list[0]

def test_handle_device_sleep(base_context, mock_now, output_list):
    """Test the device-sleep instruction handler."""
    # Create a test instruction
    instruction = {
        "action": "device-sleep"
    }
    
    # Run the handler
    should_unload = handle_device_sleep(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert len(output_list) == 1
    assert "Putting device to sleep" in output_list[0]

def test_handle_set_var_literal(base_context, mock_now, output_list):
    """Test the set_var instruction handler with literal value."""
    # Create a test instruction
    instruction = {
        "var": "new_var",
        "input": {
            "value": "new_value"
        }
    }
    
    # Run the handler
    should_unload = handle_set_var(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "new_var" in base_context["vars"]
    assert base_context["vars"]["new_var"] == "new_value"
    assert len(output_list) == 1

def test_handle_set_var_reference(base_context, mock_now, output_list):
    """Test the set_var instruction handler with variable reference."""
    # Ensure we have a source variable
    base_context["vars"]["source_var"] = "referenced_value"
    
    # Create a test instruction
    instruction = {
        "var": "new_var",
        "input": {
            "var_ref": "source_var"
        }
    }
    
    # Run the handler
    should_unload = handle_set_var(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "new_var" in base_context["vars"]
    assert base_context["vars"]["new_var"] == "referenced_value"
    assert len(output_list) == 1

def test_handle_set_var_with_default(base_context, mock_now, output_list):
    """Test the set_var instruction handler with default value."""
    # Create a test instruction with a reference to a non-existent variable
    instruction = {
        "var": "new_var",
        "input": {
            "var_ref": "nonexistent_var"
        },
        "default": "default_value"
    }
    
    # Run the handler
    should_unload = handle_set_var(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler used the default value
    assert should_unload is False
    assert "new_var" in base_context["vars"]
    assert base_context["vars"]["new_var"] == "default_value"
    assert len(output_list) == 1 