# Scheduler Event System

The Scheduler Event System enables communication between schedulers through events. Events can be thrown by one scheduler and picked up by another, allowing for coordination and complex workflows.

## Key Components

### Backend (Python)

1. **Event Storage**
   - `EventEntry` dataclass for event representation
   - Destination-specific event queues
   - Event history tracking

2. **Event API Endpoints**
   - `POST /api/schedulers/events/throw` - Create new events
   - `GET /api/schedulers/events?destination=<dest_id>` - Get events (all, or for one destination)
   - `DELETE /api/schedulers/events/<event_key>` - Clear specific events (also `DELETE /api/schedulers/events` and `/api/schedulers/events/by-id`)

3. **Event Processing**
   - Event triggers in schedule definitions
   - Event fanout to destinations
   - Single consumer vs multi-consumer events

### Frontend (React)

1. **SchedulerEventsPanel**
   - Event creation form with all options
   - Live event queue display
   - Event history display
   - Payload inspection

## Event Properties

| Property       | Description                               | Required |
|----------------|-------------------------------------------|----------|
| event          | The event key                             | Yes      |
| display_name   | User-friendly name for UI/logs            | No       |
| scope          | A destination id, a group name, or "global" (API default "global"; in a schedule, the current destination) | No |
| ttl            | Time-to-live (e.g., "60s", "5m", "2h")    | No       |
| delay          | Time to delay activation                  | No       |
| future_time    | Specific activation time                  | No       |
| payload        | Custom data object to pass with event     | No       |
| single_consumer| If true, event removed after first trigger| No       |


## Event Flow

1. **Creation**: Events are created via API or UI
2. **Storage**: Events are stored in destination-specific queues
3. **Processing**: During scheduler loop execution, events are checked for matches
4. **Consumption**: Matched events trigger actions and are removed if single_consumer=true
5. **History**: Triggered events are added to history for auditing

## Usage in Schedules

Events can be used as triggers in schedules:

```json
{
  "triggers": [
    {
      "type": "event",
      "value": "user_login",
      "trigger_actions": {
        "instructions_block": [
          {"action": "set_var", "var": "user_logged_in", "input": {"value": true}},
          {"action": "log", "message": "User login event received"}
        ]
      }
    }
  ]
}
```

## Creating Events from Schedules

Events can be created from within schedules with the `throw_event` action:

```json
{
  "action": "throw_event",
  "event": "image_generated",
  "scope": "north-screen",
  "payload": {"image": "{{ vars.generated_image }}"}
}
```

## Debugging Events

1. View active events and history in the Events Panel
2. Check scheduler logs for event processing information
3. Use the single_consumer=false option for initial testing to prevent events from being consumed

## Best Practices

1. Choose meaningful event keys that describe the event (e.g., "image_generated", "user_logged_in")
2. Use display_name for human-readable descriptions
3. Include payloads with relevant data to avoid duplicating information
4. Consider TTL settings to prevent stale events from building up
5. Use single_consumer=true for events that should only trigger once
6. Prefer destination-specific events over global events for performance 