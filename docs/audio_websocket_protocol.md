# Audio WebSocket Protocol

## Connection
- Connect to `ws://<server>:8765`
- Send initial message to identify as audio client:
```json
{
    "type": "audio"
}
```

## Message Types

### 1. Audio Data
- Send raw PCM audio data as binary WebSocket messages
- Format: 16-bit PCM, 16kHz, mono
- No additional headers or formatting needed

### 2. Control Commands
Send as JSON text messages:

#### Start Recording
```json
{
    "type": "command",
    "command": "start_recording"
}
```

#### Stop Recording
```json
{
    "type": "command",
    "command": "stop_recording"
}
```

## Example Flow
1. Connect to WebSocket
2. Send `{"type": "audio"}` to identify as audio client
3. Send `{"type": "command", "command": "start_recording"}` to begin
4. Send raw PCM audio data as binary messages
5. Send `{"type": "command", "command": "stop_recording"}` to stop
6. Close connection when done

## Error Handling
- If a command message is invalid JSON, it will be logged and ignored
- Audio data is only processed when recording is active
- Server will automatically stop recording if connection is closed

## Notes
- Audio data should be sent in chunks of approximately 5 seconds for optimal transcription
- Server will buffer audio and process it in 5-second segments
- Transcription results will be broadcast to all connected clients 