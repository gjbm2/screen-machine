# Scheduler Event Context Handling

This document explains how event payloads are handled in the scheduler, particularly how they are stored in context and cleaned up afterward.

## Overview

When an event trigger is matched in the scheduler, the event payload is made available to instructions through the `_event` context variable. This allows instruction actions to reference event data in Jinja templates, such as `{{ _event.payload.some_field }}`.

To ensure proper isolation between events and prevent overlap issues when multiple events are processed in the same scheduler cycle, the system uses a specialized context management system.

## How It Works

### 1. Event Context Storage

When an event is matched in `resolve_schedule`:

- The event payload is stored in a unique variable in the context with a name based on the event's unique ID:
  ```
  _event_{first_6_chars_of_unique_id}
  ```
  
- For backward compatibility, this data is also copied to the standard `_event` variable.

- Each instruction from the event trigger is tagged with its specific event variable name.

### 2. Instruction Execution

When each instruction is executed in `run_instruction`:

- Before processing the instruction, the system checks if it's an event-triggered instruction.

- If it is, the system copies the event data from the instruction's specific event variable back to the standard `_event` variable.

- This ensures that each instruction sees the correct event data for its trigger, even if multiple events are being processed in the same scheduler cycle.

### 3. Cleanup

After all instructions in a scheduler cycle have been processed:

- The system tracks which event variables were used during instruction execution.

- After all instructions are processed, it removes all used event variables from the context.

- This prevents event data from persisting unnecessarily in the context and avoids potential confusion in future cycles.

## Example Usage

In a schedule script, you can access event data using the standard `_event` variable:

```json
{
  "action": "set_var",
  "var_name": "last_received_message",
  "value": "Received {{ _event.payload.message }} from {{ _event.key }}"
}
```

The system automatically ensures that `_event` contains the correct data for each instruction, even when multiple events are processed in the same cycle.

## Benefits

This approach provides several benefits:

1. **Data Isolation**: Each event's data is stored separately, preventing one event from overwriting another's data.

2. **Backward Compatibility**: Existing scripts can continue to use `_event` without modification.

3. **Clean Context**: Event data is automatically removed after it's no longer needed, preventing context bloat.

4. **Multiple Events in Same Cycle**: The system correctly handles multiple events being triggered in the same scheduler cycle, ensuring each instruction gets the correct event data.

## Implementation Details

The event context handling is implemented in three main functions:

1. `resolve_schedule`: Extracts events, stores them in unique variables, and tags instructions.

2. `run_instruction`: Copies the specific event data to `_event` before processing each instruction.

3. `run_scheduler_loop`: Cleans up all event variables after processing a batch of instructions.

The implementation includes comprehensive unit and integration tests to ensure proper functionality in all scenarios. 