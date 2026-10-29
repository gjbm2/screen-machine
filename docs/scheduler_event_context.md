# Scheduler Event Context Handling

This document explains how event payloads are handled in the scheduler, particularly how they are stored in context and cleaned up afterward.

## Overview

When an event trigger is matched in the scheduler, the event payload is made available to instructions through the `_event` context variable. This allows instruction actions to reference event data in Jinja templates, such as `{{ _event.payload.some_field }}`.

To ensure proper isolation between events and prevent overlap issues when multiple events are processed in the same scheduler cycle, the system uses a specialized context management system.

## How It Works

### 1. Event Context Storage

When an event is matched in `resolve_schedule`:

- The event (`key`, `payload`, `unique_id`, `created_at`, `display_name`) is written to `_event` (in `context["vars"]` and at the top level of the context).

- Each instruction from the event trigger gets its own copy of that event, stamped on the instruction as `_event_ctx` (since 17 Oct 2026; before that `_event` was one slot, overwritten by every event matched in the same tick).

### 2. Instruction Execution

When each instruction is executed in `run_instruction`:

- If the instruction carries `_event_ctx`, it is removed from the instruction and restored as `_event`.

- This ensures that each instruction sees the correct event data for its trigger, even if multiple events are being processed in the same scheduler cycle.

### 3. Cleanup

There is no separate cleanup step: the event travels with its instructions, and `_event` is overwritten by the next event-triggered instruction.

## Example Usage

In a schedule script, you can access event data using the standard `_event` variable:

```json
{
  "action": "set_var",
  "var": "last_received_message",
  "value": "Received {{ _event.payload.message }} from {{ _event.key }}"
}
```

The system automatically ensures that `_event` contains the correct data for each instruction, even when multiple events are processed in the same cycle.

## Benefits

This approach provides several benefits:

1. **Data Isolation**: Each event's data travels with its own instructions, so one event can't overwrite another's.

2. **Backward Compatibility**: Existing scripts can continue to use `_event` without modification.

3. **Multiple Events in Same Cycle**: The system correctly handles multiple events being triggered in the same scheduler cycle, ensuring each instruction gets the correct event data.

## Implementation Details

The event context handling is implemented in two functions in `routes/scheduler.py`:

1. `resolve_schedule`: Pops matched events, sets `_event`, and stamps each of the trigger's instructions with `_event_ctx`.

2. `run_instruction`: Restores an instruction's `_event_ctx` as `_event` before processing it.

The implementation includes comprehensive unit and integration tests to ensure proper functionality in all scenarios. 