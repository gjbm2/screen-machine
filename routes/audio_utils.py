import io
import wave
import os
from utils.logger import debug, info, error, warning
import assemblyai as aai
from assemblyai.streaming.v3 import (
    StreamingClient,
    StreamingClientOptions,
    StreamingParameters,
    StreamingEvents,
    TurnEvent,
    BeginEvent,
    TerminationEvent,
    StreamingError
)
from datetime import datetime, timedelta
import numpy as np
from collections import deque
from flask import Blueprint, request
import asyncio
import threading
from typing import Optional

# Create a deque to store the last 1000 words of transcription
transcription_history: deque[str] = deque(maxlen=1000)

# Create blueprint
audio_bp = Blueprint('audio', __name__)

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_DURATION = 5  # seconds per chunk for AssemblyAI
EVENT_INTERVAL_SECONDS = 30  # Minimum seconds between transcription events

class AudioTranscriber:
    def __init__(self):
        # Set up AssemblyAI
        api_key = os.getenv('ASSEMBLY_AI_KEY')
        if not api_key:
            error("ASSEMBLY_AI_KEY environment variable not set")
            raise ValueError("ASSEMBLY_AI_KEY environment variable not set")
        
        aai.settings.api_key = api_key
        self.is_active = False  # Transcription active
        self.recording_by_destination = {}  # Track recording state by destination
        self.streaming_client: Optional[StreamingClient] = None  # Streaming client instance
        
        # Proper streaming state management based on AssemblyAI's immutable transcription approach
        self.running_transcript = ""  # Accumulates final transcripts
        self.current_turn_order = -1  # Track current turn order
        self.last_broadcast_time = datetime.now()  # Track last broadcast time
        self.broadcast_interval = 3  # Broadcast every 3 seconds
        
        self.target = "global"  # Logical scope (e.g., 'devtest')
        self.last_event_word_count = 0
        self.last_event_time = datetime.now()
        self.event_interval = EVENT_INTERVAL_SECONDS
        self._lock = threading.Lock()  # Thread safety
        self._connection_retry_count = 0
        self._max_retries = 3
        self.last_formatted_transcript = ""  # Track last formatted transcript broadcast
        self.last_broadcast_turn_order = -1   # Track which turn_order we last broadcasted
        self.client_ids: list[str] = []  # Legacy compatibility for routes expecting this attribute
        info("🎙️ Audio transcriber initialized")
    
    def is_recording_for_destination(self, destination: str) -> bool:
        """Check if audio is being recorded for a specific destination."""
        return self.recording_by_destination.get(destination, False)
    
    def get_recording_status(self) -> dict:
        """Get the recording status for all destinations based on active clients."""
        # Clear any stale recording status
        self.recording_by_destination.clear()
        
        # Only set recording status if transcription is active
        if self.is_active and self.target:
            self.recording_by_destination[self.target] = True
        
        return self.recording_by_destination
    
    def add_audio_data(self, audio_chunk: bytes) -> None:
        """Add audio data to streaming client."""
        if not audio_chunk or not self.is_active:
            return
            
        with self._lock:
            if not self.streaming_client:
                warning("⚠️ No streaming client available, attempting reconnection")
                if not self._reconnect_streaming_client():
                    error("❌ Could not reconnect streaming client")
                    return
                    
        try:
            # Normalize audio levels if they're too low
            audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
            if np.abs(audio_array).max() < 1000:  # If max amplitude is too low
                # Amplify the signal
                gain = 1000 / (np.abs(audio_array).max() + 1e-6)  # Avoid division by zero
                audio_array = np.clip(audio_array * gain, -32768, 32767).astype(np.int16)
                audio_chunk = audio_array.tobytes()
            
            # Send to streaming client
            self.streaming_client.stream(audio_chunk)
        except Exception as e:
            error(f"❌ Error streaming audio data: {e}")
            # Attempt reconnection on streaming error
            if not self._reconnect_streaming_client():
                error("❌ Failed to recover from streaming error")
        
    def _on_begin(self, client: StreamingClient, event: BeginEvent):
        """Handle session start."""
        info(f"🎙️ Streaming session started with ID: {event.id}")
        # Reset retry count on successful connection
        self._connection_retry_count = 0
        
    def _format_transcript_with_speaker(self, speaker: str, text: str) -> str:
        """Format transcript with speaker labels."""
        if not text:
            return ""
        return f"[{speaker}] {text}"

    def _broadcast_transcription(self, text: str, is_end_of_turn: bool = False):
        """Broadcast transcription to WebSocket clients."""
        if not text.strip():
            return
            
        try:
            transcription_msg = {
                "type": "transcription",
                "text": text,
                "raw_text": text,
                "target": self.target,
                "is_end_of_turn": is_end_of_turn
            }
            
            # Import here to avoid circular imports
            from overlay_ws_server import send_overlay_to_clients
            
            # Send ONCE - let the overlay server handle distribution
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            if loop.is_running():
                asyncio.create_task(send_overlay_to_clients(transcription_msg))
            else:
                loop.run_until_complete(send_overlay_to_clients(transcription_msg))
                
            debug(f"🎙️ SINGLE broadcast sent: {text[:50]}...")
                
        except Exception as e:
            error(f"❌ Failed to broadcast transcription: {e}")

    def _on_turn(self, client: StreamingClient, event: TurnEvent):
        try:
            if not event.transcript:
                return
            
            # Skip partials
            if not event.end_of_turn:
                return
            
            is_formatted = bool(getattr(event, "turn_is_formatted", False))
            if not is_formatted:
                # Wait for formatted transcript
                return
            
            turn_order = getattr(event, 'turn_order', -1)
            if turn_order == self.last_broadcast_turn_order:
                debug("🎙️ Duplicate formatted transcript (same turn_order) – skipping broadcast")
                return
            self.last_broadcast_turn_order = turn_order
            self.last_formatted_transcript = event.transcript
            
            speaker = getattr(event, "speaker_id", "A")
            formatted_text = self._format_transcript_with_speaker(speaker, event.transcript)
            
            with self._lock:
                transcription_history.extend(event.transcript.split())
                if len(transcription_history) > 900:
                    warning(f"⚠️ Transcription history approaching limit: {len(transcription_history)}/1000 words")
            
            self._broadcast_transcription(formatted_text, is_end_of_turn=True)
            self.last_broadcast_time = datetime.now()
            debug(f"🎙️ Broadcasted: {formatted_text[:50]}...")
            
            self._check_and_throw_event(formatted_text, event.transcript)
        except Exception as e:
            error(f"❌ Error in _on_turn: {e}")

    def _check_and_throw_event(self, formatted_text: str, raw_text: str):
        """Check if we should throw a transcription event."""
        try:
            # Local import once; avoids circular import at module level
            from routes.scheduler_utils import throw_event

            now_time = datetime.now()
            if (now_time - self.last_event_time).total_seconds() >= self.event_interval:
                with self._lock:
                    # Calculate full history and recent text
                    full_history = ' '.join(transcription_history)
                    recent_words = list(transcription_history)[self.last_event_word_count:]
                    recent_text = ' '.join(recent_words)

                debug(f"🎙️ Throwing _transcription event with {len(transcription_history)} words of history")

                throw_event(
                    scope=self.target,
                    key="_transcription",
                    ttl="60s",
                    payload={
                        "text": formatted_text,
                        "raw_text": full_history,
                        "recent_text": recent_text,
                        "history_size": len(transcription_history)
                    }
                )

                # Update tracking
                self.last_event_word_count = len(transcription_history)
                self.last_event_time = now_time
        except Exception as e:
            error(f"❌ Error throwing transcription event: {e}")

    def _on_terminated(self, client: StreamingClient, event: TerminationEvent):
        """Handle session termination."""
        info(f"🎙️ Streaming session ended: {event.audio_duration_seconds}s")
        
        # Attempt to reconnect if transcription is still active
        if self.is_active:
            warning("🔄 Streaming session terminated unexpectedly, attempting reconnection")
            self._reconnect_streaming_client()
        
    def _on_error(self, client: StreamingClient, err: StreamingError):
        """Handle streaming errors."""
        error(f"❌ Streaming error: {err}")
        
        # Attempt reconnection on error
        if self.is_active:
            warning("🔄 Streaming error occurred, attempting reconnection")
            self._reconnect_streaming_client()
    
    def _stop_streaming_internal(self):
        """Internal method to stop streaming (called with lock held)."""
        if self.streaming_client:
            try:
                self.streaming_client.disconnect(terminate=True)
            except Exception as e:
                warning(f"⚠️ Error disconnecting streaming client: {e}")
            finally:
                self.streaming_client = None
                
    def _reconnect_streaming_client(self):
        """Attempt to reconnect the streaming client."""
        if self._connection_retry_count >= self._max_retries:
            error(f"❌ Max reconnection attempts ({self._max_retries}) reached")
            return False
            
        try:
            self._connection_retry_count += 1
            warning(f"🔄 Attempting to reconnect streaming client (attempt {self._connection_retry_count})")
            
            # Clean up old client
            self._stop_streaming_internal()
            
            # Create new client
            self._create_streaming_client()
            return True
                
        except Exception as e:
            error(f"❌ Failed to reconnect streaming client: {e}")
            return False

    def _create_streaming_client(self):
        """Create and configure the streaming client."""
        self.streaming_client = StreamingClient(
            StreamingClientOptions(
                api_key=os.getenv('ASSEMBLY_AI_KEY'),
                api_host="streaming.assemblyai.com",
            )
        )
        
        # Set up event handlers
        self.streaming_client.on(StreamingEvents.Begin, self._on_begin)
        self.streaming_client.on(StreamingEvents.Turn, self._on_turn)
        self.streaming_client.on(StreamingEvents.Termination, self._on_terminated)
        self.streaming_client.on(StreamingEvents.Error, self._on_error)
        
        # Connect with diarization enabled
        self.streaming_client.connect(
            StreamingParameters(
                sample_rate=16000,
                format_turns=True,  # Enable diarization
            )
        )
        info("🎙️ Audio transcription streaming client connected")
    
    def start_transcription(self):
        """Start the transcription process."""
        self.is_active = True
        # Reset state for proper immutable transcription handling
        self.running_transcript = ""
        self.current_turn_order = -1
        self.last_broadcast_time = datetime.now()
        
        # Set recording status for current target
        if self.target:
            self.recording_by_destination[self.target] = True
            
        # Clear transcription history when starting a new session
        with self._lock:
            transcription_history.clear()
            self.last_event_word_count = 0
            self.last_event_time = datetime.now() - timedelta(seconds=self.event_interval + 1)  # Allow immediate event
        
        # Initialize streaming client
        self._create_streaming_client()
        info("🎙️ Audio transcription started")
    
    def stop_transcription(self):
        """Stop the transcription process."""
        self.is_active = False
        
        # Clear recording status for current target
        if self.target and self.target in self.recording_by_destination:
            self.recording_by_destination[self.target] = False
        
        # Stop streaming client
        self._stop_streaming_internal()
        
        # Reset state
        self.running_transcript = ""
        self.current_turn_order = -1
        
        info("🎙️ Audio transcription stopped")

# Global transcriber instance
_transcriber = None

def get_audio_transcriber():
    """Get or create the global audio transcriber instance."""
    global _transcriber
    if _transcriber is None:
        _transcriber = AudioTranscriber()
    return _transcriber 

@audio_bp.route('/start-transcription', methods=['POST'])
def start_transcription():
    """Start the audio transcription process."""
    get_audio_transcriber().start_transcription()
    return {'status': 'started'}

@audio_bp.route('/stop-transcription', methods=['POST'])
def stop_transcription():
    """Stop the audio transcription process."""
    get_audio_transcriber().stop_transcription()
    return {'status': 'stopped'}

@audio_bp.route('/audio', methods=['POST'])
def handle_audio():
    """Handle incoming audio data."""
    audio_data = request.get_data()
    if audio_data:
        get_audio_transcriber().add_audio_data(audio_data)
    return {'status': 'received'} 