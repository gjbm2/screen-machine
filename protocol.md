# Audio Streaming Protocol

## 1. Connection
1.1. Client connects to WebSocket at `ws://${VITE_WS_HOST}`
1.2. Client sends initial message:
```json
{
    "type": "audio",
    "target": "optional_target_id",  // If not provided, defaults to "global"
    "client_id": "unique_client_identifier"  // Required unique identifier for this client
}
```

## 2. Audio Streaming
2.1. Client streams raw binary audio data (16-bit PCM, 16000Hz, mono)
2.2. Target is inherited from initial connection message
2.3. Server processes audio in 5-second chunks
2.4. Server transcribes audio using AssemblyAI
2.5. Multiple clients can stream to the same target - their client_ids will be associated with the transcription

## 3. Transcription Response
3.1. Server sends transcription messages:
```json
{
    "type": "transcription",
    "text": "formatted transcription with speaker labels and annotations",
    "raw_text": "plain transcription text",
    "target": "target_id",
    "client_ids": ["client_a_id", "client_b_id"]
}
```

3.2. Rich Text Format:
The transcription text is formatted with the following features:
- Speaker labels: `[A]`, `[B]`, etc.
- Sentiment indicators: 😊 (positive), 😞 (negative)
- Named entities: **entity** (ENTITY_TYPE)
- Important highlights: ⭐ at start of line
- Categories: _category1_, _category2_ at top of transcript
- Each utterance on a new line

Example:
```
Categories: _Education_, _Technology_

[A] Welcome to the Python class. 😊
[B] I'm excited to learn about **Python** (PROGRAMMING_LANGUAGE).
⭐ [A] Let's start with the basics of programming.
```

Note: The formatting is based on AssemblyAI's real-time transcription features:
- Speaker diarization identifies different speakers
- Sentiment analysis detects positive/negative sentiment
- Entity detection identifies named entities and their types
- Auto-highlights mark important moments
- IAB categories provide content classification

## 4. Event Triggering
4.1. Server triggers scheduler events using existing throw_event system
4.2. Event structure:
   - Target: Used as event destination (not in payload)
   - Event key: "_transcription" (system event, following _user_interacting convention)
   - Event payload:
```json
{
    "text": "formatted transcription with speaker labels and annotations",
    "raw_text": "full transcription history",
    "recent_text": "new words since last event",
    "client_ids": ["client_a_id", "client_b_id"]
}
```

## 5. Target Resolution
5.1. Server uses existing routes.utils functions to resolve targets
5.2. Available targets can be queried via existing `/publish-destinations` endpoint

## 6. WebSocket Message Flow
6.1. Client Connection Flow:
   - Client connects to WebSocket
   - Client sends initial "audio" type message with client_id and optional target
   - Server acknowledges by adding client to audio_clients set
   - Server logs: "🎵 Audio stream started"

6.2. Audio Data Flow:
   - Client sends binary audio data
   - Server processes in 5-second chunks
   - Server logs: "🎙️ Received {len(msg)} bytes of audio data from client"

6.3. Transcription Flow:
   - Server creates transcription message
   - Server logs: "🎙️ Creating WebSocket message for transcription: target='{target}', text='{text}', clients={count}"
   - Server broadcasts to all connected clients
   - Server logs: "🎙️ Broadcasting transcription: target='{target}', text='{text}', clients={count}"
   - Server logs: "✅ Successfully sent transcription to {count} client(s)"

6.4. Error Handling:
   - Connection errors: "❌ Connection closed: {address}"
   - Message errors: "❌ Error handling WebSocket connection: {error}"
   - Broadcast errors: "❌ Error sending message to clients: {error}"
   - Task creation errors: "❌ Failed to create WebSocket broadcast task: {error}"

## 7. Client Debugging
7.1. Connection Issues:
   - Check WebSocket URL is correct
   - Verify initial message format
   - Check for connection errors in server logs

7.2. Audio Streaming Issues:
   - Verify audio format (16-bit PCM, 16000Hz, mono)
   - Check audio data is being sent as binary
   - Monitor server logs for audio data reception

7.3. Transcription Issues:
   - Check server logs for transcription creation
   - Verify transcription messages are being broadcast
   - Monitor client WebSocket message reception
   - Check transcription history is being maintained

7.4. Target Issues:
   - Verify target exists in publish-destinations
   - Check target resolution in server logs
   - Monitor event triggering for correct target 

# Overlay Testing Protocol

## Overview
This section describes how to test overlays using the `/test-overlay` endpoint. Follow these steps exactly to ensure your overlay displays correctly.

## Steps to Test an Overlay

1. **Prepare Your Overlay HTML File**
   - Place your overlay HTML file in the `routes/overlays/` directory.
   - **IMPORTANT:** The file name in your API call MUST match the file name on disk exactly. For example, if your file is named `!!overlay_alert.html`, you must use `"htmlFile": "!!overlay_alert.html"` in your request.

2. **Send a POST Request to `/test-overlay`**
   - Use the following JSON structure:
     ```json
     {
       "screens": ["north-screen"],
       "htmlFile": "!!overlay_alert.html",
       "duration": 10000,
       "position": "bottom-center",
       "substitutions": "{\n\t'{{ALERT_TEXT}}': 'Hello world',\n\t'{{PROMPT_TEXT}}': 'Cat on a bicycle'\n}",
       "clear": false
     }
     ```
   - **WARNING:** If your file is named differently (e.g., `overlay_alert.html`), you MUST rename the file on disk to match your request, or update your request to match the file name.

3. **Check the Server Logs**
   - After sending the request, check the server logs for messages like:
     ```
     [test_overlay] INFO: Data: {'screens': ['north-screen'], 'htmlFile': '!!overlay_alert.html', ...}
     [send_overlay] DEBUG: Sending overlay message: {'screens': ['north-screen'], 'html': '<div>...</div>', ...}
     ```
   - If you see `'html': '!!overlay_alert.html'` instead of actual HTML content, the file was not found or read correctly.

4. **Verify the Display**
   - The overlay should appear on the `north-screen` display.
   - If nothing shows up, double-check:
     - The file name in your request matches the file on disk.
     - The file contains valid HTML with the correct placeholders.
     - The `screens` field matches the display's ID exactly.

## Common Mistakes
- **File Name Mismatch:** Using `"htmlFile": "overlay_alert.html"` when the file is actually named `!!overlay_alert.html`.
- **Empty or Invalid HTML:** The overlay file is empty or contains invalid HTML.
- **Wrong Display ID:** The `screens` field does not match the display's ID.

## Example
- **Correct Request:**
  ```json
  {
    "screens": ["north-screen"],
    "htmlFile": "!!overlay_alert.html",
    "duration": 10000,
    "position": "bottom-center",
    "substitutions": "{\n\t'{{ALERT_TEXT}}': 'Hello world',\n\t'{{PROMPT_TEXT}}': 'Cat on a bicycle'\n}",
    "clear": false
  }
  ```
- **Incorrect Request (Will Fail):**
  ```json
  {
    "screens": ["north-screen"],
    "htmlFile": "overlay_alert.html",
    "duration": 10000,
    "position": "bottom-center",
    "substitutions": "{\n\t'{{ALERT_TEXT}}': 'Hello world',\n\t'{{PROMPT_TEXT}}': 'Cat on a bicycle'\n}",
    "clear": false
  }
  ```

## Troubleshooting
- If the overlay does not appear, check the server logs for errors.
- Ensure the file exists, is readable, and contains valid HTML.
- Verify that the display client is connected and listening for WebSocket messages. 