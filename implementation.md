# Implementation Plan

## 1. AudioTranscriber Updates
1.1. Add target field to AudioTranscriber class
1.2. Add client_id tracking to AudioTranscriber class
1.3. Store target and client_ids from initial connection
1.4. Pass target and client_ids through to transcription history

## 2. WebSocket Server Compatibility
2.1. Review existing overlay_ws_server.py message handling
2.2. Ensure new audio message types don't conflict with existing message types
2.3. Maintain backward compatibility:
   - Keep existing message type handling unchanged
   - Add new message types without modifying existing ones
   - Ensure existing clients won't break with new message formats
2.4. Add message type validation to prevent conflicts
2.5. Document all message types in overlay_ws_server.py
2.6. Add logging for new message types to help with debugging

## 3. WebSocket Handler Updates
3.1. Add target and client_id parsing to initial connection message
3.2. Add target and client_ids to transcription messages
3.3. Add target and client_ids to transcription history
3.4. Implement client_id tracking for shared target transcriptions

## 4. Event Integration
4.1. Add throw_event call after successful transcription
4.2. Use target as event destination
4.3. Pass transcribed text and client_ids in event payload 