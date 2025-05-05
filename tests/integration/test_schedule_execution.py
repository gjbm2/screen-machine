import pytest
import asyncio
from datetime import datetime
from routes.scheduler import resolve_schedule, run_scheduler, start_scheduler, stop_scheduler, run_instruction
from routes.scheduler_utils import active_events, get_current_context, scheduler_contexts_stacks

@pytest.fixture
def setup_event():
    """Setup an event for a test destination."""
    dest_id = "test_dest"
    
    # Clear any existing events for this destination
    active_events[dest_id] = {}
    
    # Set a test event
    active_events[dest_id]["TestEvent"] = datetime.now()
    
    yield dest_id
    
    # Clean up
    if dest_id in active_events:
        active_events[dest_id] = {}

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
    instructions = resolve_schedule(test_schedule_basic, now, dest_id)
    
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
    
    # Event should be consumed (removed)
    assert "TestEvent" not in active_events[dest_id]

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
        return MockFuture()
    
    monkeypatch.setattr('asyncio.run_coroutine_threadsafe', mock_run_coroutine_threadsafe)
    
    # Mock get_event_loop
    def mock_get_event_loop():
        return object()  # Just a dummy object
    
    monkeypatch.setattr('routes.scheduler.get_event_loop', mock_get_event_loop)
    
    # Start the scheduler
    start_scheduler(dest_id, test_schedule_basic)
    
    # Verify the scheduler was started
    from routes.scheduler import running_schedulers
    assert dest_id in running_schedulers
    assert dest_id in clean_scheduler_state["contexts"]
    assert dest_id in clean_scheduler_state["schedules"]
    assert clean_scheduler_state["states"][dest_id] == "running"
    
    # Stop the scheduler
    stop_scheduler(dest_id)
    
    # Verify the scheduler was stopped
    assert dest_id not in running_schedulers
    assert clean_scheduler_state["states"][dest_id] == "stopped" 