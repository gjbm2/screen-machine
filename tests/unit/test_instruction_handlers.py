import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import os
from routes.scheduler_utils import process_instruction_jinja
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
    """Test the simplified devise_prompt instruction handler."""
    # Create a test instruction with the new simplified schema
    instruction = {
        "action": "devise_prompt",
        "input": "This is a test input",
        "output_var": "result_text"
    }
    
    # Run the handler
    should_unload = handle_devise_prompt(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert "result_text" in base_context["vars"]
    assert base_context["vars"]["result_text"] == "This is a test input"
    assert len(output_list) == 1
    assert "Devised result:" in output_list[0]

def test_handle_devise_prompt_with_history(base_context, mock_now, output_list):
    """Test the simplified devise_prompt instruction with history tracking."""
    # Create a test instruction with history
    instruction = {
        "action": "devise_prompt",
        "input": "Process this text",
        "output_var": "result_text",
        "history_var": "process_history"
    }
    
    # Run the handler
    should_unload = handle_devise_prompt(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly and history was updated
    assert should_unload is False
    assert "result_text" in base_context["vars"]
    assert base_context["vars"]["result_text"] == "Process this text"
    assert "process_history" in base_context["vars"]
    assert isinstance(base_context["vars"]["process_history"], list)
    assert len(base_context["vars"]["process_history"]) == 1
    assert "timestamp" in base_context["vars"]["process_history"][0]
    assert "input" in base_context["vars"]["process_history"][0]
    assert "output" in base_context["vars"]["process_history"][0]
    assert base_context["vars"]["process_history"][0]["input"] == "Process this text"
    assert base_context["vars"]["process_history"][0]["output"] == "Process this text"

def test_handle_devise_prompt_backward_compatibility(base_context, mock_now, output_list):
    """Test that the devise_prompt handler uses the new format (not backward compatible)."""
    # Create a test instruction with the new format
    instruction = {
        "action": "devise_prompt",
        "input": "Legacy input",
        "output_var": "legacy_result",
        "history_var": "legacy_history"
    }
    
    # Run the handler
    should_unload = handle_devise_prompt(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler correctly handled the new format
    assert should_unload is False
    assert "legacy_result" in base_context["vars"]
    assert "legacy_history" in base_context["vars"]
    assert isinstance(base_context["vars"]["legacy_history"], list)
    assert len(base_context["vars"]["legacy_history"]) == 1

def test_handle_generate(base_context, mock_now, output_list):
    """Test the generate instruction handler."""
    # Create a test instruction
    instruction = {
        "input": {
            "prompt": "A test prompt"
        },
        "refiner": "test_refiner",
        "workflow": "test_workflow",
        "history_var": "generation_history"
    }

    # Run the handler
    handle_generate(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the context was updated with the results
    assert "last_generated" in base_context
    assert "generation_history" in base_context["vars"]
    assert len(base_context["vars"]["generation_history"]) > 0
    
    # Check log output
    assert any("Generated image" in msg for msg in output_list)

def test_handle_animate(base_context, mock_now, output_list):
    """Test the animate instruction handler."""
    # Setup last_generated in context
    base_context["last_generated"] = "previous_image.jpg"

    # Create a test instruction
    instruction = {
        "input": {
            "prompt": "Animate this"
        },
        "refiner": "animator"
    }

    # Run the handler
    handle_animate(instruction, base_context, mock_now, output_list, "test_dest")

    # Check for expected output
    assert any("animation" in msg.lower() for msg in output_list)

def test_handle_display(base_context, mock_now, output_list):
    """Test the display instruction handler."""
    # Test with different display modes
    modes = ["Next", "Random", "Blank"]
    
    for mode in modes:
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
        assert len(output_list) >= 1
        
        # Check that our output contains expected text
        found_display_message = False
        for msg in output_list:
            if "display" in msg.lower():
                found_display_message = True
                break
        assert found_display_message, f"Display message not found in output for mode {mode}: {output_list}"

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

def test_handle_set_var_with_jinja_template(base_context, mock_now, output_list):
    """Test the set_var instruction handler with Jinja templating."""
    # Add a variable to the context for templating
    base_context["vars"]["source_var"] = "template_value"
    
    # Create a test instruction with Jinja template in the value
    instruction = {
        "var": "new_var",
        "input": {
            "value": "prefix_{{ source_var }}_suffix"
        }
    }
    
    # Process the instruction with Jinja templating first, like scheduler.py does
    processed_instruction = process_instruction_jinja(instruction, base_context, "test_dest")
    
    # Run the handler with the processed instruction
    should_unload = handle_set_var(processed_instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler correctly processed the template
    assert should_unload is False
    assert "new_var" in base_context["vars"]
    assert base_context["vars"]["new_var"] == "prefix_template_value_suffix"
    assert len(output_list) == 1
    
    # Try with conditional Jinja syntax
    instruction = {
        "var": "conditional_var",
        "input": {
            "value": "{% if source_var == 'template_value' %}condition_met{% else %}condition_not_met{% endif %}"
        }
    }
    
    # Clear output list for second test
    output_list.clear()
    
    # Process with Jinja templating first
    processed_instruction = process_instruction_jinja(instruction, base_context, "test_dest")
    
    # Run the handler
    should_unload = handle_set_var(processed_instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that conditional templating worked
    assert should_unload is False
    assert "conditional_var" in base_context["vars"]
    assert base_context["vars"]["conditional_var"] == "condition_met"
    assert len(output_list) == 1

def test_handle_set_var_reset_context(base_context, mock_now, output_list):
    """Test the set_var instruction with var=null to reset the context."""
    # Add some variables to the context first
    base_context["vars"]["var1"] = "value1"
    base_context["vars"]["var2"] = "value2"
    base_context["vars"]["var3"] = 123
    
    # Create a reset instruction (var=null)
    reset_instruction = {
        "action": "set_var",
        "var": None,
        "input": {"value": None}
    }
    
    # Run the handler
    should_unload = handle_set_var(reset_instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    assert len(output_list) == 1
    assert "Reset context" in output_list[0]
    
    # Check that the vars dictionary is now empty
    assert len(base_context["vars"]) == 0
    
    # Now set a variable in the reset context to ensure it works
    set_instruction = {
        "action": "set_var",
        "var": "new_var",
        "input": {"value": "new_value"}
    }
    
    # Reset output list for second test
    output_list.clear()
    
    # Run the handler
    should_unload = handle_set_var(set_instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that setting works after reset
    assert should_unload is False
    assert len(output_list) == 1
    assert "Set new_var to new_value" in output_list[0]
    assert len(base_context["vars"]) == 1
    assert base_context["vars"]["new_var"] == "new_value"

@patch('routes.scheduler_handlers.get_generation_service')
def test_handle_generate_with_jinja_template(mock_get_generation, base_context, mock_now, output_list):
    """Test the generate instruction handler with Jinja templating."""
    # Set up mock generation service
    mock_service = MagicMock()
    mock_service.handle_image_generation.return_value = [{"message": "test_output.jpg"}]
    mock_get_generation.return_value = mock_service
    
    # Add variables to the context for templating
    base_context["vars"]["subject"] = "dog"
    base_context["vars"]["style"] = "watercolor"
    
    # Create a test instruction with Jinja template in the prompt
    instruction = {
        "action": "generate",
        "input": {
            "prompt": "A {{ subject }} painted in {{ style }} style"
        }
    }
    
    # Process the instruction with Jinja templating first, like scheduler.py does
    processed_instruction = process_instruction_jinja(instruction, base_context, "test_dest")
    
    # Run the handler with the processed instruction
    handle_generate(processed_instruction, base_context, mock_now, output_list, "test_dest")
    
    # Verify the prompt was properly templated
    assert mock_service.handle_image_generation.called
    call_args = mock_service.handle_image_generation.call_args[1]
    assert "input_obj" in call_args
    assert "data" in call_args["input_obj"] 
    assert "prompt" in call_args["input_obj"]["data"]
    assert call_args["input_obj"]["data"]["prompt"] == "A dog painted in watercolor style"
    
    # Check that last_generated was updated
    assert "last_generated" in base_context
    assert base_context["last_generated"] == "test_output.jpg"

def test_jinja_dynamic_property_names():
    """Test Jinja templating in property names like variable names."""
    # Create a context with variables
    context = {
        "vars": {
            "prefix": "test",
            "suffix": "var",
            "index": 42
        }
    }
    
    # Create an instruction with Jinja in the property name
    instruction = {
        "action": "set_var",
        "var": "{{ prefix }}_{{ suffix }}_{{ index }}",
        "input": {
            "value": "Property name from template"
        }
    }
    
    # Process the instruction with Jinja
    processed = process_instruction_jinja(instruction, context, "test_dest")
    
    # Check that the var property was correctly templated
    assert processed["var"] == "test_var_42"
    
    # Test with nested properties
    nested_instruction = {
        "action": "import_var",
        "var_name": "source_{{ suffix }}",
        "as": "local_{{ prefix }}_{{ index }}",
        "scope": "global"
    }
    
    # Process the nested instruction
    processed_nested = process_instruction_jinja(nested_instruction, context, "test_dest")
    
    # Check that both properties were correctly templated
    assert processed_nested["var_name"] == "source_var"
    assert processed_nested["as"] == "local_test_42"

def test_standardized_history_vars():
    """Test that all three instructions (devise_prompt, generate, animate) handle history_var consistently."""
    # Set up a context with empty vars
    context = {"vars": {}}
    now = datetime.now()
    output = []
    
    # 1. Test devise_prompt with history_var
    devise_instruction = {
        "action": "devise_prompt",
        "input": "Test input for devise",
        "output_var": "result",
        "history_var": "operation_history"
    }
    
    handle_devise_prompt(devise_instruction, context, now, output, "test_dest")
    
    # Check history was recorded correctly
    assert "operation_history" in context["vars"]
    assert len(context["vars"]["operation_history"]) == 1
    history_entry = context["vars"]["operation_history"][0]
    assert history_entry["type"] == "devise_prompt"
    assert history_entry["input"] == "Test input for devise"
    assert "timestamp" in history_entry
    
    # 2. Test generate with history_var using our mock
    with patch('routes.scheduler_handlers.get_generation_service') as mock_service:
        mock_generator = MagicMock()
        mock_generator.return_value = [{"message": "test.jpg"}]
        mock_service.return_value = mock_generator
        
        generate_instruction = {
            "action": "generate",
            "input": {"prompt": "Test prompt for generate"},
            "history_var": "operation_history"
        }
        
        handle_generate(generate_instruction, context, now, output, "test_dest")
        
        # Check history was appended correctly
        assert len(context["vars"]["operation_history"]) == 2
        history_entry = context["vars"]["operation_history"][1]
        assert history_entry["type"] == "generation"
        assert history_entry["prompt"] == "Test prompt for generate"
        assert "timestamp" in history_entry
    
    # 3. Test animate with history_var using our mock
    with patch('routes.scheduler_handlers.get_animation_service') as mock_service:
        mock_animator = MagicMock()
        mock_animator.animate.return_value = {"animation_id": "anim_123"}
        mock_service.return_value = mock_animator
        
        # Set last_generated for the animate function to use
        context["last_generated"] = "source_image.jpg"
        
        animate_instruction = {
            "action": "animate",
            "input": {
                "prompt": "Test prompt for animate"
            },
            "history_var": "operation_history"
        }
        
        handle_animate(animate_instruction, context, now, output, "test_dest")
        
        # Check history was appended correctly
        assert len(context["vars"]["operation_history"]) == 3
        history_entry = context["vars"]["operation_history"][2]
        assert history_entry["type"] == "animation"
        assert history_entry["prompt"] == "Test prompt for animate"
        assert history_entry["image_path"] == "source_image.jpg"
        assert "timestamp" in history_entry 