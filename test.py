#!/usr/bin/env python3

from datetime import datetime
from routes.scheduler_handlers import handle_set_var, handle_random_choice, handle_devise_prompt

# Test handle_set_var with input.value
def test_handle_set_var_input_value():
    context = {"vars": {}}
    now = datetime.now()
    output = []
    instruction = {
        "var": "test_var",
        "input": {"value": "test_value"}
    }
    
    should_unload = handle_set_var(instruction, context, now, output, "test_dest")
    
    print(f"Should unload: {should_unload}")
    print(f"Context vars: {context['vars']}")
    print(f"Output: {output}")
    
    assert should_unload is False
    assert "test_var" in context["vars"]
    assert context["vars"]["test_var"] == "test_value"
    assert len(output) == 1
    
# Test handle_random_choice
def test_handle_random_choice():
    context = {"vars": {}}
    now = datetime.now()
    output = []
    instruction = {
        "var": "random_result",
        "choices": ["choice1", "choice2", "choice3"]
    }
    
    should_unload = handle_random_choice(instruction, context, now, output, "test_dest")
    
    print(f"Should unload: {should_unload}")
    print(f"Context vars: {context['vars']}")
    print(f"Output: {output}")
    
    assert should_unload is False
    assert "random_result" in context["vars"]
    assert context["vars"]["random_result"] in instruction["choices"]
    assert len(output) == 1

# Test handle_devise_prompt
def test_handle_devise_prompt():
    context = {"vars": {}}
    now = datetime.now()
    output = []
    instruction = {
        "action": "devise_prompt",
        "input": "This is an input that needs processing",
        "output_var": "processed_result"
    }
    
    should_unload = handle_devise_prompt(instruction, context, now, output, "test_dest")
    
    print(f"Should unload: {should_unload}")
    print(f"Context vars: {context['vars']}")
    print(f"Output: {output}")
    
    assert should_unload is False
    assert "processed_result" in context["vars"]
    assert context["vars"]["processed_result"] == "This is an input that needs processing"
    assert len(output) == 1

# Run the tests
if __name__ == "__main__":
    print("Testing handle_set_var with input.value:")
    test_handle_set_var_input_value()
    print("\nTesting handle_random_choice:")
    test_handle_random_choice()
    print("\nTesting handle_devise_prompt:")
    test_handle_devise_prompt()
    print("\nAll tests passed!") 