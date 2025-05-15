import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import os
from routes.scheduler_utils import process_instruction_jinja, scheduler_logs, log_schedule, reset_trigger_execution_timestamps, last_trigger_executions
from routes.scheduler_handlers import (
    handle_random_choice, handle_generate, 
    handle_animate, handle_display, handle_wait, handle_unload,
    handle_device_media_sync, handle_device_wake, handle_device_sleep,
    handle_set_var, handle_reason
)
from routes.scheduler import resolve_schedule

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
    modes = ["Next", "Random", "Previous", "Blank"]
    
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
    """Test that all three instructions (reason, generate, animate) handle history_var consistently."""
    context = {"vars": {}}
    now = datetime.now()
    output = []
    
    # 1. Test reason with history_var
    reason_instruction = {
        "action": "reason",
        "reasoner": "test_reasoner",
        "text_input": "Test input",
        "output_vars": ["result_var"],
        "history_var": "test_history"
    }
    
    with patch('routes.openai.openai_prompt') as mock_openai:
        # Updated to use array format for outputs
        mock_openai.return_value = {
            "outputs": [
                "Test result"
            ]
        }
        
        handle_reason(reason_instruction, context, now, output, "test_dest")
    
    # Check history structure
    assert "test_history" in context["vars"]
    assert len(context["vars"]["test_history"]) == 1
    history_entry = context["vars"]["test_history"][0]
    assert "timestamp" in history_entry
    assert history_entry["type"] == "reason"
    assert "text_input" in history_entry
    assert "outputs" in history_entry
    assert "result_var" in history_entry["outputs"]
    assert history_entry["outputs"]["result_var"] == "Test result"
    
    # 2. Test generate with history_var 
    generate_instruction = {
        "action": "generate",
        "input": {"prompt": "Test prompt"},
        "history_var": "test_history"
    }
    
    with patch('routes.scheduler_handlers.get_generation_service') as mock_get_gen:
        mock_service = MagicMock()
        mock_service.handle_image_generation.return_value = [{"message": "source_image.jpg"}]
        mock_get_gen.return_value = mock_service
        
        handle_generate(generate_instruction, context, now, output, "test_dest")
    
    # Check history structure - should have 2 entries now
    assert len(context["vars"]["test_history"]) == 2
    history_entry = context["vars"]["test_history"][1]  # Second entry
    assert "timestamp" in history_entry
    assert history_entry["type"] == "generation"
    assert "prompt" in history_entry
    assert "image_url" in history_entry
    assert history_entry["prompt"] == "Test prompt"
    
    # 3. Test animate with history_var
    animate_instruction = {
        "action": "animate",
        "input": {"prompt": "Test animation prompt"},
        "history_var": "test_history"
    }
    
    with patch('routes.scheduler_handlers.get_animation_service') as mock_get_anim:
        mock_service = MagicMock()
        mock_service.animate.return_value = {"animation_id": "anim123"}
        mock_get_anim.return_value = mock_service
        
        handle_animate(animate_instruction, context, now, output, "test_dest")
    
    # Check history structure - should have 3 entries now
    assert len(context["vars"]["test_history"]) == 3
    history_entry = context["vars"]["test_history"][2]  # Third entry
    assert "timestamp" in history_entry
    assert history_entry["type"] == "animation"
    assert "prompt" in history_entry
    assert "image_path" in history_entry
    assert "animation_id" in history_entry
    assert history_entry["animation_id"] == "anim123"

@patch('routes.openai.openai_prompt')
def test_handle_reason(mock_openai_prompt, base_context, mock_now, output_list):
    """Test the reason instruction handler."""
    # Set up mock
    mock_openai_prompt.return_value = {
        "outputs": [
            "Generated content"
        ],
        "explanation": "This is an explanation of the reasoning process."
    }
    
    # Create a test instruction
    instruction = {
        "action": "reason",
        "reasoner": "test_reasoner",
        "text_input": "Test input text",
        "image_inputs": [],
        "output_vars": ["test_var"],
        "history_var": "reason_history"
    }
    
    # Run the handler
    should_unload = handle_reason(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler behaved correctly
    assert should_unload is False
    
    # Verify OpenAI call
    mock_openai_prompt.assert_called_once()
    openai_args = mock_openai_prompt.call_args[1]
    assert openai_args["user_prompt"] == "Test input text"
    assert "schema" in openai_args
    assert "images" in openai_args
    assert openai_args["images"] is None  # Empty image list gets converted to None
    
    # Verify context variables
    assert "test_var" in base_context["vars"]
    assert base_context["vars"]["test_var"] == "Generated content"
    
    # Verify history
    assert "reason_history" in base_context["vars"]
    assert isinstance(base_context["vars"]["reason_history"], list)
    assert len(base_context["vars"]["reason_history"]) == 1
    
    history_entry = base_context["vars"]["reason_history"][0]
    assert history_entry["type"] == "reason"
    assert history_entry["reasoner"] == "test_reasoner"
    assert history_entry["text_input"] == "Test input text"
    assert "outputs" in history_entry
    assert "test_var" in history_entry["outputs"]
    assert history_entry["outputs"]["test_var"] == "Generated content"
    assert "explanation" in history_entry
    assert history_entry["explanation"] == "This is an explanation of the reasoning process."
    
    # Verify log output
    assert len(output_list) >= 3  # Should have at least start, variable set, and completion logs
    assert any("Reasoning with 'test_reasoner'" in msg for msg in output_list)
    assert any("Set test_var to result" in msg for msg in output_list)
    assert any("Completed reasoning" in msg for msg in output_list)

def test_handle_set_var_with_top_level_default(base_context, mock_now, output_list):
    """Test the set_var instruction handler with a top-level default value (without using input.var_ref)."""
    # Create a test instruction with a top-level default field
    instruction = {
        "var": "new_var",
        "default": "default_from_top_level"
    }
    
    # Run the handler
    should_unload = handle_set_var(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that the handler used the top-level default value
    assert should_unload is False
    assert "new_var" in base_context["vars"]
    assert base_context["vars"]["new_var"] == "default_from_top_level"
    assert len(output_list) == 1
    assert "Set new_var to default_from_top_level" in output_list[0]
    
    # Test with no value at all (should fail gracefully)
    output_list.clear()
    instruction = {
        "var": "another_var"
        # No value or default specified
    }
    
    # Run the handler
    should_unload = handle_set_var(instruction, base_context, mock_now, output_list, "test_dest")
    
    # Check that it handles the error correctly
    assert should_unload is False
    assert len(output_list) == 1
    assert "Error in set_var: could not determine value" in output_list[0]

def test_log_schedule_no_duplicates():
    """Ensure log_schedule does not duplicate entries when output is the same list as scheduler_logs."""
    dest_id = "dup_test_dest"
    # Ensure clean state
    scheduler_logs[dest_id] = []
    # Use the same list as output
    output_ref = scheduler_logs[dest_id]
    now = datetime.now()
    # Call log_schedule
    log_schedule("Test duplication message", dest_id, now, output_ref)
    # After first call there should be exactly 1 entry
    assert len(scheduler_logs[dest_id]) == 1
    # Call again with a different message
    log_schedule("Another message", dest_id, now, output_ref)
    # Now there should be 2 entries, not 4
    assert len(scheduler_logs[dest_id]) == 2
    # Clean up
    del scheduler_logs[dest_id]

def test_reset_trigger_exec_timestamps_no_immediate_execution():
    """After resetting execution timestamps the repeating schedule should NOT fire again until the next interval (regression for issue #5)."""
    from routes.scheduler_utils import reset_trigger_execution_timestamps, last_trigger_executions
    from routes.scheduler import resolve_schedule
    import datetime as _dt

    dest_id = "reset_test_dest"

    # Minimal repeating schedule: every 45 minutes starting 01:00
    test_schedule = {
        "triggers": [
            {
                "type": "day_of_week",
                "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                "scheduled_actions": [
                    {
                        "time": "01:00",
                        "repeat_schedule": {"every": "45"},
                        "trigger_actions": {
                            "instructions_block": [
                                {"action": "set_var", "var": "dummy", "input": {"value": "1"}}
                            ]
                        }
                    }
                ]
            }
        ]
    }

    # Clear any existing data for this test
    if dest_id in last_trigger_executions:
        last_trigger_executions[dest_id] = {}

    # First run: 08:32 Tuesday 7 Jan 2025 (just 2 minutes after the 08:30 boundary, within grace period)
    now1 = _dt.datetime(2025, 1, 7, 8, 32)
    instructions_1 = resolve_schedule(test_schedule, now1, dest_id, True)
    assert instructions_1, "Initial run should match schedule"

    # Instead of relying on _scheduler_metadata, manually track the executed triggers
    # Get first trigger ID using the same logic as scheduler_utils.py
    schedule_id = "e49b71602bbc1c22cf98f59dce38d879"
    expected_execution_time = _dt.datetime(2025, 1, 7, 8, 30)
    trigger_id = f"{schedule_id}_{expected_execution_time.isoformat()}"
    
    # Mark it as executed
    last_trigger_executions[dest_id][trigger_id] = now1

    # After a few minutes, should not match again
    now2 = _dt.datetime(2025, 1, 7, 8, 35)
    instructions_2 = resolve_schedule(test_schedule, now2, dest_id, False)
    assert not instructions_2, "Should not match again so soon"

    # Reset execution timestamps
    reset_trigger_execution_timestamps(dest_id)

    # After reset, a time within 5 minutes of a boundary (08:34 is 4 minutes after 08:30)
    # should still execute
    now3 = _dt.datetime(2025, 1, 7, 8, 34)
    instructions_3 = resolve_schedule(test_schedule, now3, dest_id, True)
    assert instructions_3, "Should execute within grace period after timestamp reset"

    # But a time outside the grace period should not execute, even after reset
    now4 = _dt.datetime(2025, 1, 7, 8, 47)  # 17 minutes after 08:30
    instructions_4 = resolve_schedule(test_schedule, now4, dest_id, False)
    assert not instructions_4, "Should not execute outside grace period even after reset"

    # Next scheduled time should work
    now5 = _dt.datetime(2025, 1, 7, 9, 16)  # 1 minute after 09:15
    instructions_5 = resolve_schedule(test_schedule, now5, dest_id, True)
    assert instructions_5, "Next scheduled interval should match"

    # Clean up global state
    if dest_id in last_trigger_executions:
        del last_trigger_executions[dest_id]

def test_handle_set_var_no_duplicate_logging(base_context):
    """Ensure handle_set_var does not create duplicate log entries when output list is scheduler_logs[dest]."""
    from routes.scheduler_utils import scheduler_logs
    dest_id = "dup_test_dest_handler"
    # Prepare output list that is exactly the scheduler log list
    scheduler_logs[dest_id] = []
    output_ref = scheduler_logs[dest_id]
    now = datetime.now()
    instruction = {
        "action": "set_var",
        "var": "testvar",
        "input": {"value": "abc"}
    }
    # First call should add exactly one entry
    handle_set_var(instruction, base_context, now, output_ref, dest_id)
    assert len(scheduler_logs[dest_id]) == 1, "Expected one log entry after first set_var"
    # Second call with different value should add one more entry (no duplicates)
    instruction["input"]["value"] = "def"
    handle_set_var(instruction, base_context, now, output_ref, dest_id)
    assert len(scheduler_logs[dest_id]) == 2, "Expected exactly two distinct log entries after second set_var"
    # Clean up
    del scheduler_logs[dest_id] 