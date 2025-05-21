# Scheduler Purge Instruction

The `purge` instruction allows you to schedule automatic cleanup of buckets. This is useful for maintenance to prevent buckets from growing too large over time.

## Configuration

You can add a purge instruction to any schedule with the following parameters:

```json
{
  "action": "purge",
  "days": 30,
  "include_favorites": false,
  "dontwait": false
}
```

### Parameters

- **days** (optional): Only delete files older than this many days. If not specified, all files will be deleted based on the favorites setting.
- **include_favorites** (optional, default: `false`): Whether to remove favorite files as well. By default, favorite files are kept.
- **dontwait** (optional, default: `false`): When set to `true`, the scheduler will not wait for the purge operation to complete before proceeding to the next instruction.

## Example Uses

### Weekly Cleanup Schedule

Here's an example of a weekly schedule that runs every Sunday at midnight to purge files older than 30 days:

```json
{
  "triggers": [
    {
      "type": "day_of_week",
      "days": ["Sunday"],
      "scheduled_actions": [
        {
          "time": "00:00",
          "trigger_actions": {
            "instructions_block": [
              {
                "action": "log",
                "message": "Starting weekly bucket cleanup"
              },
              {
                "action": "purge",
                "days": 30,
                "include_favorites": false
              },
              {
                "action": "log",
                "message": "Weekly cleanup completed"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

### Monthly Purge with Event Trigger

This example shows how to set up an event-triggered purge that can be manually activated:

```json
{
  "triggers": [
    {
      "type": "event",
      "value": "maintenance_purge",
      "trigger_actions": {
        "instructions_block": [
          {
            "action": "purge",
            "days": 90,
            "include_favorites": true
          },
          {
            "action": "log",
            "message": "Deep cleanup completed, including favorites older than 90 days"
          }
        ]
      }
    }
  ]
}
```

You can trigger this with the scheduler event API:

```bash
curl -X POST http://localhost:5000/api/scheduler/events \
  -H "Content-Type: application/json" \
  -d '{"scope": "your-bucket-id", "key": "maintenance_purge", "ttl": "60s"}'
```

## Behavior

The purge instruction operates on the bucket specified by the `publish_destination` field in the scheduler configuration. The operation:

1. Loads all files in the bucket
2. If `days` is specified, filters out files that are newer than the specified age
3. If `include_favorites` is `false` (default), keeps files marked as favorites
4. Deletes all remaining files from the bucket

The operation logs all deleted files and returns a count of removed files. 