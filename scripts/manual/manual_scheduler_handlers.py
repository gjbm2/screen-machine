#!/usr/bin/env python3

from datetime import datetime
import json
from routes.scheduler_handlers import handle_set_var, handle_random_choice, handle_reason

# Test handle_set_var
def test_handle_set_var():
    context = {"vars": {"existing_var": "existing_value"}}
    now = datetime.now()
    output = []
    publish_destination = "test_dest"
    
    # Test basic variable setting
    instruction = {
        "var": "test_var",
        "value": "test_value"
    }
    
    should_unload = handle_set_var(instruction, context, now, output, publish_destination)
    
    print(f"Result of handle_set_var: should_unload={should_unload}")
    print(f"Context after handle_set_var: {json.dumps(context, indent=2)}")
    print(f"Output after handle_set_var: {output}")
    
    # Test variable reset
    reset_instruction = {
        "var": None
    }
    
    output.clear()
    should_unload = handle_set_var(reset_instruction, context, now, output, publish_destination)
    
    print(f"Result of handle_set_var with reset: should_unload={should_unload}")
    print(f"Context after reset: {json.dumps(context, indent=2)}")
    print(f"Output after reset: {output}")

# Test handle_random_choice
def test_handle_random_choice():
    context = {"vars": {}}
    now = datetime.now()
    output = []
    publish_destination = "test_dest"
    
    instruction = {
        "var": "random_var",
        "choices": ["option1", "option2", "option3"]
    }
    
    should_unload = handle_random_choice(instruction, context, now, output, publish_destination)
    
    print(f"Result of handle_random_choice: should_unload={should_unload}")
    print(f"Context after handle_random_choice: {json.dumps(context, indent=2)}")
    print(f"Output after handle_random_choice: {output}")

# Test handle_reason
def test_handle_reason():
    import routes.openai
    import routes.utils
    from unittest.mock import patch
    
    context = {"vars": {}}
    now = datetime.now()
    output = []
    publish_destination = "test_dest"
    
    instruction = {
        "action": "reason",
        "reasoner": "test_reasoner",
        "text_input": "Test input for reasoning",
        "output_vars": ["result_var"],
        "history_var": "reason_history"
    }
    
    # Mock the openai and dict_substitute functions
    with patch('routes.openai.openai_prompt') as mock_openai, \
         patch('routes.utils.dict_substitute') as mock_substitute:
        
        mock_substitute.side_effect = ["Test system prompt", '{"type":"object"}']
        mock_openai.return_value = {"outputs": {"result_var": "Generated result"}}
        
        should_unload = handle_reason(instruction, context, now, output, publish_destination)
    
    print(f"Result of handle_reason: should_unload={should_unload}")
    print(f"Context after handle_reason: {json.dumps(context, indent=2)}")
    print(f"Output after handle_reason: {output}")

# Run tests
if __name__ == "__main__":
    print("Testing handle_set_var:")
    test_handle_set_var()
    
    print("\nTesting handle_random_choice:")
    test_handle_random_choice()
    
    print("\nTesting handle_reason:")
    test_handle_reason() 