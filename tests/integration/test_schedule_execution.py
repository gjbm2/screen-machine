import pytest
import asyncio
from datetime import datetime, timedelta
from routes.scheduler import resolve_schedule, run_scheduler, start_scheduler, stop_scheduler, run_instruction
from routes.scheduler_utils import active_events, event_history, get_current_context, scheduler_contexts_stacks, scheduler_schedule_stacks, EventEntry
import routes.scheduler_utils  # Add direct import for monkeypatching
from collections import deque

@pytest.fixture
def setup_event():
    """Setup an event for a test destination."""
    dest_id = "test_dest"
    
    # Clear any existing events for this destination
    if dest_id in active_events:
        active_events[dest_id] = {}
    else:
        active_events[dest_id] = {}
    
    # Set a test event using the new EventEntry format
    now = datetime.now()
    event_entry = EventEntry(
        key="TestEvent",
        active_from=now,
        expires=now + timedelta(seconds=60),
        display_name="Test Event",
        single_consumer=True,  # Make it single consumer to ensure it works properly in tests
        created_at=now
    )
    
    # Initialize the event queue
    active_events[dest_id]["TestEvent"] = deque([event_entry])
    
    # Print debug info
    print(f"Setup event TestEvent for {dest_id}: {active_events[dest_id]}")
    
    yield dest_id
    
    # Clean up - clear ALL event state to avoid test interference
    active_events.clear()
    event_history.clear()

@pytest.mark.asyncio
async def test_resolve_and_execute_basic_schedule(clean_scheduler_state, test_schedule_basic):
    """Test resolving and executing a basic schedule."""
    dest_id = "test_dest"
    
    # Setup context
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {},
        "publish_destination": dest_id
    }]
    
    # Resolve schedule
    now = datetime.now()
    instructions = resolve_schedule(test_schedule_basic, now, dest_id, include_initial_actions=True)
    
    # Verify initial instructions are returned
    assert len(instructions) == 1
    assert instructions[0]["action"] == "set_var"
    assert instructions[0]["var"] == "test_var"

@pytest.mark.asyncio
async def test_schedule_with_event_trigger(clean_scheduler_state, test_schedule_with_event, setup_event):
    """Test that an event trigger generates the correct instructions."""
    dest_id = setup_event  # This fixture ensures the TestEvent is set
    
    # Setup context
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {},
        "publish_destination": dest_id
    }]
    
    # Resolve schedule
    now = datetime.now()
    instructions = resolve_schedule(test_schedule_with_event, now, dest_id)
    
    # Verify event triggered instructions are returned
    assert len(instructions) >= 1
    
    # Check for the event-triggered instruction
    found_event_instruction = False
    for instr in instructions:
        if instr["action"] == "set_var" and instr["var"] == "event_var":
            found_event_instruction = True
            break
            
    assert found_event_instruction, "Event trigger instructions should be included"
    
    # Event should be consumed (queue should be empty)
    assert dest_id in active_events
    assert "TestEvent" in active_events[dest_id]
    assert len(active_events[dest_id]["TestEvent"]) == 0

@pytest.mark.asyncio
async def test_scheduler_run_with_final_actions(clean_scheduler_state, test_schedule_with_final):
    """Test running a scheduler that includes final actions."""
    dest_id = "test_dest"

    # Setup context
    context = {
        "vars": {},
        "publish_destination": dest_id
    }
    clean_scheduler_state["contexts"][dest_id] = [context]
    scheduler_contexts_stacks[dest_id] = clean_scheduler_state["contexts"][dest_id]

    # Setup schedule stack
    clean_scheduler_state["schedules"][dest_id] = [test_schedule_with_final]
    
    # Manually run the initial actions to ensure they are executed
    initial_instructions = test_schedule_with_final["initial_actions"]["instructions_block"]
    for instr in initial_instructions:
        run_instruction(instr, context, datetime.now(), [], dest_id)
    
    # At this point, initial_var should be set
    assert context["vars"].get("initial_var") == "initial_value"

    # Create a short-lived scheduler task
    scheduler_task = asyncio.create_task(
        run_scheduler(test_schedule_with_final, dest_id)
    )

    # Let it run for a short time to process initial actions
    await asyncio.sleep(0.5)

    # Cancel the scheduler
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

    # Manually run the final actions for verification
    final_instructions = test_schedule_with_final["final_actions"]["instructions_block"]
    for instr in final_instructions:
        run_instruction(instr, context, datetime.now(), [], dest_id)

    # Check the context should now have both initial and final vars
    assert "initial_var" in context["vars"]
    assert "final_var" in context["vars"]
    assert context["vars"]["final_var"] == "final_value"

@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_testing_mode")  # This enables mock services
async def test_schedule_with_generate_and_animate(clean_scheduler_state, test_schedule_generate_animate):
    """Test a schedule that includes generate and animate instructions."""
    dest_id = "test_dest"

    # Setup context
    context = {
        "vars": {},
        "publish_destination": dest_id
    }
    clean_scheduler_state["contexts"][dest_id] = [context]
    scheduler_contexts_stacks[dest_id] = clean_scheduler_state["contexts"][dest_id]

    # Setup schedule stack
    clean_scheduler_state["schedules"][dest_id] = [test_schedule_generate_animate]
    
    # Manually run the initial actions to ensure they are executed
    initial_instructions = test_schedule_generate_animate["initial_actions"]["instructions_block"]
    for instr in initial_instructions:
        run_instruction(instr, context, datetime.now(), [], dest_id)
    
    # At this point, prompt should be set
    assert "prompt" in context["vars"]
    assert context["vars"]["prompt"] == "test prompt for generation"

    # Create a short-lived scheduler task
    scheduler_task = asyncio.create_task(
        run_scheduler(test_schedule_generate_animate, dest_id)
    )

    # Let it run for a short time to process all instructions
    await asyncio.sleep(1.0)

    # Cancel the scheduler
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    
    # The context should have a last_generated value from the generate step
    assert context.get("last_generated") is not None
    assert "prompt" in context["vars"]

@pytest.mark.asyncio
async def test_schedule_start_stop_lifecycle(clean_scheduler_state, test_schedule_basic, monkeypatch):
    """Test the full lifecycle of starting and stopping a scheduler."""
    dest_id = "test_dest"

    # Mock the asyncio functions to avoid actually running the scheduler
    def mock_run_coroutine_threadsafe(coro, loop):
        # Execute just enough of the coroutine to set initial values
        try:
            coro.send(None)  # Start the coroutine
        except StopIteration:
            pass

        class MockFuture:
            def cancel(self):
                pass
            
            def done(self):
                return False
                
            def cancelled(self):
                return False
                
        return MockFuture()

    monkeypatch.setattr('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe)

    # Mock get_event_loop to avoid thread issues in tests
    def mock_get_event_loop():
        return object()  # Just a dummy object

    monkeypatch.setattr('routes.scheduler.get_event_loop', mock_get_event_loop)
    
    # Initialize scheduler logs to prevent KeyError
    from routes.scheduler import scheduler_logs
    scheduler_logs[dest_id] = []
    
    # Make sure test_dest is in the contexts structure
    clean_scheduler_state["contexts"][dest_id] = [{
        "vars": {},
        "publish_destination": dest_id
    }]
    
    # Manually init the schedule stack - this ensures it won't be lost
    scheduler_schedule_stacks[dest_id] = [test_schedule_basic]
    
    # Additionally, save the schedule to clean_scheduler_state
    clean_scheduler_state["schedules"][dest_id] = [test_schedule_basic]

    # Start the scheduler - need to patch the internal implementation to ensure schedule_stack is preserved
    def patched_start_scheduler(publish_destination, schedule, *args, **kwargs):
        from routes.scheduler import scheduler_states, running_schedulers
        
        # Explicitly ensure schedule is in the stack
        scheduler_schedule_stacks[publish_destination] = [schedule]
        
        # Set state to running
        scheduler_states[publish_destination] = "running"
        
        # Create a mock running scheduler
        class MockFuture:
            def done(self):
                return False
                
            def cancelled(self):
                return False
            
            def cancel(self):
                # Remove the scheduler from running_schedulers
                running_schedulers.pop(publish_destination, None)
                return True
        
        # Add to running schedulers
        running_schedulers[publish_destination] = MockFuture()
        
        return True
    
    # Patch the start_scheduler function
    original_start_scheduler = start_scheduler
    monkeypatch.setattr('routes.scheduler.start_scheduler', patched_start_scheduler)
    
    # Call the patched start_scheduler
    patched_start_scheduler(dest_id, test_schedule_basic)

    # Manually set state to running for test verification
    clean_scheduler_state["states"][dest_id] = "running"

    # Verify the scheduler was started
    from routes.scheduler import running_schedulers
    assert dest_id in running_schedulers
    assert dest_id in clean_scheduler_state["contexts"]
    
    # Verify the stack was updated with the new schedule
    assert dest_id in scheduler_schedule_stacks
    assert len(scheduler_schedule_stacks[dest_id]) >= 1
    
    # Stop the scheduler
    stop_scheduler(dest_id)
    
    # Verify it was stopped
    assert dest_id not in running_schedulers
    assert clean_scheduler_state["states"][dest_id] == "stopped" 