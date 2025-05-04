import pytest
from datetime import datetime
from unittest.mock import patch, Mock
from routes.scheduler import run_instruction
from routes.scheduler_utils import scheduler_contexts_stacks

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
        assert len(output_list) == 1

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
        assert len(output_list) == 1

    @patch('routes.scheduler_handlers.display_from_bucket')
    def test_run_instruction_display(self, mock_display, base_context, mock_now, output_list):
        """Test running a display instruction."""
        # Mock the display function to return success
        mock_display.return_value = {"success": True}
        
        instruction = {
            "action": "display",
            "show": "Next",
            "silent": False
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it worked correctly
        assert should_unload is False
        assert len(output_list) == 1
        mock_display.assert_called_once()

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
        assert len(output_list) == 1

    def test_run_instruction_unload(self, base_context, mock_now, output_list):
        """Test running an unload instruction."""
        instruction = {
            "action": "unload"
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it returns True to signal unload
        assert should_unload is True
        assert len(output_list) == 1

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
            assert len(output_list) == 1

    @patch('routes.scheduler.get_context_stack')
    @patch('routes.scheduler.update_scheduler_state')
    def test_run_instruction_updates_global_context(self, mock_update_state, mock_get_context, base_context, mock_now, output_list, clean_scheduler_state):
        """Test that run_instruction updates the global context after execution."""
        dest_id = "test_dest"
        
        # Create a context stack for this destination
        clean_scheduler_state["contexts"][dest_id] = [base_context]
        
        # Make mock_get_context return our stack
        mock_get_context.return_value = clean_scheduler_state["contexts"][dest_id]
        
        # Create a simple instruction that modifies the context
        instruction = {
            "action": "set_var",
            "var": "test_var",
            "input": {"value": "test_value"}
        }
        
        # Run the instruction
        run_instruction(instruction, base_context, mock_now, output_list, dest_id)
        
        # Verify that update_scheduler_state was called to persist the changes
        mock_update_state.assert_called_once()
        
        # The context stack should have been updated
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
        assert len(output_list) == 1
        assert "Unknown action" in output_list[0]

    @patch('routes.scheduler_handlers.handle_generate')
    def test_run_instruction_with_exception(self, mock_handle_generate, base_context, mock_now, output_list):
        """Test running an instruction that throws an exception."""
        # Set up the mock to raise an exception
        mock_handle_generate.side_effect = Exception("Test exception")
        
        instruction = {
            "action": "generate",
            "input": {"prompt": "Test prompt"}
        }
        
        # Run the instruction
        should_unload = run_instruction(instruction, base_context, mock_now, output_list, "test_dest")
        
        # Verify it handled the exception correctly
        assert should_unload is False
        assert len(output_list) == 1
        assert "Error" in output_list[0]
        assert "Test exception" in output_list[0] 