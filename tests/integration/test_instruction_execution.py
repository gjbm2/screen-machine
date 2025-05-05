import pytest
from datetime import datetime
from unittest.mock import patch, Mock, MagicMock
import os
from routes.scheduler import run_instruction
from routes.scheduler_utils import scheduler_contexts_stacks

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
        "last_generated": None
    }

@pytest.fixture
def output_list():
    return []

class TestInstructionExecution:
    """Test the integration between instruction handler selection and execution."""
    
    def test_run_instruction_set_var(self, base_context, mock_now, output_list):
        """Test running a set_var instruction."""
        instruction = {
            "action": "set_var",
            "var": "test_var",
            "input": {"value": "test_value"}
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it worked correctly
        assert should_unload is False
        assert "test_var" in base_context["vars"]
        assert base_context["vars"]["test_var"] == "test_value"
        assert len(output_list) >= 1

    def test_run_instruction_random_choice(self, base_context, mock_now, output_list):
        """Test running a random_choice instruction."""
        instruction = {
            "action": "random_choice",
            "var": "random_result",
            "choices": ["choice1", "choice2", "choice3"]
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it worked correctly
        assert should_unload is False
        assert "random_result" in base_context["vars"]
        assert base_context["vars"]["random_result"] in instruction["choices"]
        assert len(output_list) >= 1

    def test_run_instruction_display(self, base_context, mock_now, output_list):
        """Test running a display instruction."""
        instruction = {
            "action": "display",
            "show": "Next",
            "silent": False
        }

        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")

        # Verify expected results
        assert should_unload is False
        assert len(output_list) >= 1
        
        # Check for display-related output
        found_display_message = False
        for msg in output_list:
            if "display" in msg.lower():
                found_display_message = True
                break
        assert found_display_message, f"Display message not found in output: {output_list}"

    def test_run_instruction_wait(self, base_context, mock_now, output_list):
        """Test running a wait instruction."""
        instruction = {
            "action": "wait",
            "duration": 5
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it worked correctly
        assert should_unload is False
        assert "wait_until" in base_context
        assert len(output_list) >= 1

    def test_run_instruction_unload(self, base_context, mock_now, output_list):
        """Test running an unload instruction."""
        instruction = {
            "action": "unload"
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it returns True to signal unload
        assert should_unload is True
        assert len(output_list) >= 1

    def test_run_instruction_device_commands(self, base_context, mock_now, output_list):
        """Test running device-related instructions."""
        for action in ["device-media-sync", "device-wake", "device-sleep"]:
            # Reset output list
            output_list.clear()
            
            instruction = {
                "action": action
            }
            
            # Run the instruction
            should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
            
            # Verify it worked correctly
            assert should_unload is False
            assert len(output_list) >= 1

    def test_run_instruction_updates_global_context(self, base_context, mock_now, output_list, clean_scheduler_state):
        """Test that run_instruction updates the global context after execution."""
        dest_id = "test_dest"
        
        # Create a context stack for this destination
        clean_scheduler_state["contexts"][dest_id] = [base_context]
        
        # Make sure scheduler_contexts_stacks has our context
        scheduler_contexts_stacks[dest_id] = clean_scheduler_state["contexts"][dest_id]
        
        # Create a simple instruction that modifies the context
        instruction = {
            "action": "set_var",
            "var": "test_var",
            "input": {"value": "test_value"}
        }
        
        # Run the instruction
        run_instruction(instruction, base_context, mock_now, output_list, dest_id)
        
        # Check that the context was updated
        assert "test_var" in clean_scheduler_state["contexts"][dest_id][0]["vars"]
        assert clean_scheduler_state["contexts"][dest_id][0]["vars"]["test_var"] == "test_value"

    def test_run_instruction_unknown_action(self, base_context, mock_now, output_list):
        """Test running an instruction with an unknown action."""
        instruction = {
            "action": "nonexistent_action"
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it handled the error correctly
        assert should_unload is False
        assert len(output_list) >= 1
        assert "Unknown action" in output_list[0]

    def test_run_instruction_with_exception(self, base_context, mock_now, output_list):
        """Test running an instruction that throws an exception."""
        # Create a custom instruction that will cause an error
        instruction = {
            "action": "generate",
            "input": {
                "prompt": ""  # Empty prompt should cause an error
            }
        }

        # Clear any existing output
        output_list.clear()

        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")

        # Verify expected behavior
        assert should_unload is False
        assert len(output_list) >= 1
        
        # Check for error-related message
        error_message_found = False
        for msg in output_list:
            if "error" in msg.lower() or "no prompt" in msg.lower():
                error_message_found = True
                break
        assert error_message_found, f"Error message not found in output: {output_list}" 