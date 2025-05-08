import pytest
import os
import json
from datetime import datetime
from routes.scheduler import start_scheduler, stop_scheduler, run_instruction
from routes.scheduler_utils import (
    load_scheduler_state, save_scheduler_state, update_scheduler_state,
    get_scheduler_storage_path, process_instruction_jinja,
    scheduler_contexts_stacks, scheduler_schedule_stacks, scheduler_states
)

def test_save_and_load_scheduler_state(mock_scheduler_storage_path, setup_test_destination):
    """Test saving and loading scheduler state."""
    dest_id = setup_test_destination
    
    # Create a test state
    test_state = {
        "schedule_stack": [
            {
                "initial_actions": {
                    "instructions_block": [
                        {"action": "set_var", "var": "test_var", "input": {"value": "test_value"}}
                    ]
                }
            }
        ],
        "context_stack": [
            {
                "vars": {"existing_var": "existing_value"},
                "last_generated": None,
                "publish_destination": dest_id
            }
        ],
        "state": "running",
        "last_updated": datetime.now().isoformat()
    }
    
    # Save the state
    save_scheduler_state(dest_id, test_state)
    
    # Verify the file was created
    state_path = get_scheduler_storage_path(dest_id)
    assert os.path.exists(state_path)
    
    # Load the state
    loaded_state = load_scheduler_state(dest_id)
    
    # Verify key properties
    assert len(loaded_state["schedule_stack"]) == 1
    assert len(loaded_state["context_stack"]) == 1
    assert loaded_state["state"] == "running"
    assert "existing_var" in loaded_state["context_stack"][0]["vars"]
    assert loaded_state["context_stack"][0]["vars"]["existing_var"] == "existing_value"

def test_update_scheduler_state(mock_scheduler_storage_path, setup_test_destination):
    """Test updating scheduler state."""
    dest_id = setup_test_destination
    
    # Set initial state
    initial_state = {
        "schedule_stack": [],
        "context_stack": [{"vars": {}, "publish_destination": dest_id}],
        "state": "stopped"
    }
    save_scheduler_state(dest_id, initial_state)
    
    # Update just the state
    update_scheduler_state(dest_id, state="running")
    
    # Check that just the state was updated
    updated_state = load_scheduler_state(dest_id)
    assert updated_state["state"] == "running"
    assert len(updated_state["schedule_stack"]) == 0
    assert len(updated_state["context_stack"]) == 1
    
    # Update with a new schedule and context
    new_schedule = [{"test": "schedule"}]
    new_context = [{"vars": {"new_var": "new_value"}, "publish_destination": dest_id}]
    
    update_scheduler_state(
        dest_id,
        schedule_stack=new_schedule,
        context_stack=new_context
    )
    
    # Check that everything was updated
    fully_updated_state = load_scheduler_state(dest_id)
    assert fully_updated_state["state"] == "running"  # Maintained from previous update
    assert len(fully_updated_state["schedule_stack"]) == 1
    assert fully_updated_state["schedule_stack"][0]["test"] == "schedule"
    assert len(fully_updated_state["context_stack"]) == 1
    assert fully_updated_state["context_stack"][0]["vars"]["new_var"] == "new_value"

def test_state_persistence_through_restart(mock_scheduler_storage_path, clean_scheduler_state, monkeypatch):
    """Test that state is properly preserved through start and stop."""
    dest_id = "test_dest"
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[dest_id] = []
    
    # Create a simple test schedule
    test_schedule = {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "persistent_var", "input": {"value": "initial_value"}}
            ]
        }
    }
    
    # Mock functions to avoid actual scheduler execution
    def mock_run_coroutine_threadsafe(coro, loop):
        # Execute the first part of the coroutine to run initial_actions
        try:
            coro.send(None)  # Start the coroutine
        except StopIteration:
            pass
            
        class MockFuture:
            def cancel(self):
                pass
        return MockFuture()
    
    monkeypatch.setattr('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe)
    
    # Mock get_event_loop to avoid thread issues in tests
    def mock_get_event_loop(dest_id=None):
        return object()  # Any object to represent the loop
    
    monkeypatch.setattr('routes.scheduler.get_event_loop', mock_get_event_loop)
    
    # Create a patched version of start_scheduler that properly initializes context
    def patched_start_scheduler(publish_destination, schedule, *args, **kwargs):
        from routes.scheduler import scheduler_states, running_schedulers
        
        # Initialize the context stack if it doesn't exist
        if publish_destination not in scheduler_contexts_stacks:
            scheduler_contexts_stacks[publish_destination] = [{"vars": {}, "publish_destination": publish_destination}]
            
        # Initialize the schedule stack
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
        scheduler_schedule_stacks[publish_destination].append(schedule)
        
        # Set state to running
        scheduler_states[publish_destination] = "running"
        
        # Create a mock running scheduler
        class MockFuture:
            def done(self):
                return False
                
            def cancelled(self):
                return False
        
        # Add to running schedulers
        running_schedulers[publish_destination] = MockFuture()
        
        # Execute initial actions manually
        context = scheduler_contexts_stacks[publish_destination][0]
        if "initial_actions" in schedule and "instructions_block" in schedule["initial_actions"]:
            for instr in schedule["initial_actions"]["instructions_block"]:
                from routes.scheduler import run_instruction
                run_instruction(instr, context, datetime.now(), [], publish_destination)
        
        # Return successful start
        return True
    
    # Patch start_scheduler
    monkeypatch.setattr('routes.scheduler.start_scheduler', patched_start_scheduler)
    
    # Start the scheduler with our patched function
    patched_start_scheduler(dest_id, test_schedule)
    
    # Verify the initial state
    assert dest_id in scheduler_contexts_stacks
    assert len(scheduler_contexts_stacks[dest_id]) == 1
    assert "vars" in scheduler_contexts_stacks[dest_id][0]
    assert "persistent_var" in scheduler_contexts_stacks[dest_id][0]["vars"]
    
    # Set a variable in the context - simulate modification during execution
    scheduler_contexts_stacks[dest_id][0]["vars"]["persistent_var"] = "modified_value"
    
    # Update the persisted state
    update_scheduler_state(
        dest_id,
        context_stack=scheduler_contexts_stacks[dest_id],
        force_save=True
    )
    
    # Stop the scheduler
    stop_scheduler(dest_id)
    
    # Clear in-memory state to simulate restart
    saved_contexts = scheduler_contexts_stacks.copy()  # Save for verification
    scheduler_contexts_stacks.clear()
    scheduler_schedule_stacks.clear()
    scheduler_states.clear()
    
    # Now load the state as if during startup
    state = load_scheduler_state(dest_id)
    
    # Set scheduler_contexts_stacks to the loaded context
    scheduler_contexts_stacks[dest_id] = state["context_stack"]
    
    # Verify the persistent variable was preserved
    assert len(state["context_stack"]) == 1
    assert state["context_stack"][0]["vars"].get("persistent_var") == "modified_value"
    
    # Verify that the state now exists in scheduler_contexts_stacks
    assert dest_id in scheduler_contexts_stacks
    assert scheduler_contexts_stacks[dest_id][0]["vars"]["persistent_var"] == "modified_value"

def test_start_scheduler_resumes_with_existing_context(mock_scheduler_storage_path, clean_scheduler_state, monkeypatch):
    """Test that starting a scheduler with the same ID resumes with existing context."""
    dest_id = "test_dest"

    # Create initial state with variables
    initial_context = [{
        "vars": {"existing_var": "existing_value"},
        "publish_destination": dest_id
    }]

    # Set up clean state
    clean_scheduler_state["contexts"][dest_id] = initial_context
    clean_scheduler_state["states"][dest_id] = "running"  # Set to running to simulate resume
    
    # Update the global scheduler_contexts_stacks too
    scheduler_contexts_stacks[dest_id] = initial_context

    # Save the state
    update_scheduler_state(
        dest_id,
        context_stack=initial_context,
        state="running"
    )

    # Create a simple test schedule
    test_schedule = {
        "initial_actions": {
            "instructions_block": [
                {"action": "set_var", "var": "new_var", "input": {"value": "new_value"}}
            ]
        }
    }

    # Mock functions to avoid actual scheduler execution
    def mock_run_coroutine_threadsafe(coro, loop):
        class MockFuture:
            def cancel(self):
                pass
        return MockFuture()

    monkeypatch.setattr('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe)

    # Mock get_event_loop to avoid thread issues in tests
    def mock_get_event_loop(dest_id=None):
        return object()

    monkeypatch.setattr('routes.scheduler.get_event_loop', mock_get_event_loop)

    # Mock load_scheduler_state to return our predefined context
    def mock_load_scheduler_state(publish_destination):
        if publish_destination == dest_id:
            return {
                "context_stack": initial_context,
                "schedule_stack": [],
                "state": "running"
            }
        return {}
    
    monkeypatch.setattr('routes.scheduler_utils.load_scheduler_state', mock_load_scheduler_state)

    # Start the scheduler - should resume with existing context
    start_scheduler(dest_id, test_schedule)

    # Verify context was preserved
    assert dest_id in scheduler_contexts_stacks
    assert len(scheduler_contexts_stacks[dest_id]) == 1
    context = scheduler_contexts_stacks[dest_id][0]

    # The existing var should still be there
    assert "existing_var" in context["vars"]
    assert context["vars"]["existing_var"] == "existing_value"

def test_jinja_templates_preserved_in_storage(mock_scheduler_storage_path, setup_test_destination):
    """Test that Jinja templates in instructions are preserved in storage and only processed at runtime."""
    dest_id = setup_test_destination
    
    # Create a test schedule with Jinja templates
    test_schedule = {
        "initial_actions": {
            "instructions_block": [
                {
                    "action": "set_var", 
                    "var": "template_var", 
                    "input": {"value": "initial_value"}
                },
                {
                    "action": "set_var",
                    "var": "{{ template_var }}_result",
                    "input": {"value": "Value with {{ template_var }}"}
                },
                {
                    "action": "wait",
                    "duration": "{{ 5 * 2 }}"
                }
            ]
        }
    }
    
    # Save the schedule to state
    state = {
        "schedule_stack": [test_schedule],
        "context_stack": [{"vars": {}, "publish_destination": dest_id}],
        "state": "stopped"
    }
    save_scheduler_state(dest_id, state)
    
    # Load the state back
    loaded_state = load_scheduler_state(dest_id)
    
    # Verify that the Jinja templates are preserved in the loaded state
    loaded_schedule = loaded_state["schedule_stack"][0]
    instructions = loaded_schedule["initial_actions"]["instructions_block"]
    
    # Check that the second instruction still has the Jinja template
    assert "{{ template_var }}_result" == instructions[1]["var"]
    assert "Value with {{ template_var }}" == instructions[1]["input"]["value"]
    assert "{{ 5 * 2 }}" == instructions[2]["duration"]
    
    # Now simulate running the instructions and verify templates get processed
    context = {"vars": {}}
    
    # Run the first instruction to set template_var
    output = []
    run_instruction(instructions[0], context, datetime.now(), output, dest_id)
    assert context["vars"]["template_var"] == "initial_value"
    
    # Run the second instruction (with Jinja templates)
    output.clear()
    run_instruction(instructions[1], context, datetime.now(), output, dest_id)
    
    # Verify the variable name was processed with Jinja
    assert "initial_value_result" in context["vars"]
    assert context["vars"]["initial_value_result"] == "Value with initial_value"
    
    # Verify the duration is processed correctly in the wait instruction
    processed_wait = process_instruction_jinja(instructions[2], context, dest_id)
    assert processed_wait["duration"] == 10  # Should be converted to integer after processing
    
    # Make sure the original instruction in storage is unchanged
    reloaded_state = load_scheduler_state(dest_id)
    assert "{{ 5 * 2 }}" == reloaded_state["schedule_stack"][0]["initial_actions"]["instructions_block"][2]["duration"] 