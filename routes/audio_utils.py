import io
import wave
import os
from utils.logger import debug, info, error, warning
import assemblyai as aai
from datetime import datetime, timedelta
import numpy as np
from collections import deque
from flask import Blueprint, request

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
        self.audio_buffer = bytearray()
        self.bytes_per_chunk = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS * 5  # 5 seconds of audio
        self.min_chunk_size = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS * 2  # 2 seconds minimum
        self.target = "global"  # Default target
        self.client_ids = set()  # Set of client IDs for this target
        self.last_event_word_count = 0  # Track how many words have been sent in the last event
        self.last_event_time = datetime.now()
        self.event_interval = EVENT_INTERVAL_SECONDS
        info("🎙️ Audio transcriber initialized")
    
    def add_client(self, client_id: str, target: str = "global") -> None:
        """Add a client to the transcriber."""
        self.client_ids.add(client_id)
        self.target = target
        info(f"🎙️ Added client {client_id} to target {target}")
    
    def remove_client(self, client_id: str) -> None:
        """Remove a client from the transcriber."""
        # Store the target before removing the client
        target = self.target
        self.client_ids.discard(client_id)
        
        info(f"🎙️ Removed client {client_id}")
        
        # If no clients left at all, stop transcription
        if not self.client_ids:
            self.is_active = False
            # Clear recording status for this target
            if target in self.recording_by_destination:
                self.recording_by_destination[target] = False
            info("🎙️ No more clients connected, stopping transcription")
    
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
        """Add audio data to the buffer."""
        if not audio_chunk:
            return
            
        # Convert bytes to list of hex values for debugging
        hex_values = [hex(b) for b in audio_chunk[:16]]
        
        # Convert to numpy array for analysis
        audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
        
        # Add to buffer
        self.audio_buffer.extend(audio_chunk)
        
        # Check if we have enough data for transcription
        if len(self.audio_buffer) >= self.bytes_per_chunk:
            # Get the chunk for transcription
            chunk = bytes(self.audio_buffer[:self.bytes_per_chunk])
            # Remove the chunk from buffer
            self.audio_buffer = self.audio_buffer[self.bytes_per_chunk:]
            
            # Process the chunk
            self._process_audio_chunk(chunk)
    
    def _process_audio_chunk(self, audio_chunk: bytes) -> None:
        """Process a chunk of audio data and send it for transcription."""
        try:
            # Normalize audio levels if they're too low
            audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
            if np.abs(audio_array).max() < 1000:  # If max amplitude is too low
                # Amplify the signal
                gain = 1000 / (np.abs(audio_array).max() + 1e-6)  # Avoid division by zero
                audio_array = np.clip(audio_array * gain, -32768, 32767).astype(np.int16)
                audio_chunk = audio_array.tobytes()
            
            # Create WAV file in memory
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(SAMPLE_WIDTH)
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(audio_chunk)
            
            # Save WAV file to disk for debugging
            wav_dir = "wav"
            os.makedirs(wav_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            wav_filename = f"{wav_dir}/audio_{timestamp}_{len(audio_chunk)}bytes.wav"
            with open(wav_filename, 'wb') as f:
                f.write(wav_buffer.getvalue())
            
            wav_buffer.seek(0)
            
            # Create transcriber and transcribe
            transcriber = aai.Transcriber()
            try:
                # Create transcription config – keep it minimal for robust output
                config = aai.TranscriptionConfig(
                    language_code="en"
                )
                
                transcript = transcriber.transcribe(wav_buffer, config=config)
                
                # Only log the transcription text and add to history
                if transcript and transcript.text:
                    # Build a plain speaker-labelled transcript (no extra features)
                    rich_text = []
                    if transcript.utterances:
                        for utt in transcript.utterances:
                            rich_text.append(f"[{utt.speaker}] {utt.text}")
                    
                    # Add words to history
                    transcription_history.extend(transcript.text.split())

                    # Prepare WebSocket message
                    from overlay_ws_server import send_overlay_to_clients
                    import asyncio
                    
                    # Use raw text if no utterances were found
                    formatted_text = "\n".join(rich_text) if rich_text else transcript.text
                    
                    transcription_msg = {
                        "type": "transcription",
                        "text": formatted_text,  # Use formatted text or raw text
                        "raw_text": transcript.text,  # Keep the raw text for reference
                        "target": self.target,
                        "client_ids": list(self.client_ids)
                    }
                    
                    # Broadcast to all connected clients
                    try:
                        asyncio.create_task(send_overlay_to_clients(transcription_msg))
                    except Exception as e:
                        error(f"❌ Failed to create WebSocket broadcast task: {e}")
                    
                    # Decide whether to emit an event (rate-limited)
                    now_time = datetime.now()
                    if (now_time - self.last_event_time).total_seconds() >= self.event_interval:
                        from routes.scheduler_utils import throw_event
                        debug(f"🎙️ Throwing _transcription event with {len(transcription_history)} words of history")
                        
                        # Calculate full history and recent text
                        full_history = ' '.join(transcription_history)
                        recent_words = list(transcription_history)[self.last_event_word_count:]
                        recent_text = ' '.join(recent_words)
                        
                        throw_event(
                            scope=self.target,
                            key="_transcription",
                            ttl="60s",
                            payload={
                                "text": formatted_text,  # Use formatted text or raw text
                                "raw_text": full_history,  # Keep the raw text for reference
                                "recent_text": recent_text,
                                "client_ids": list(self.client_ids)
                            }
                        )
                        # Update tracking
                        self.last_event_word_count = len(transcription_history)
                        self.last_event_time = now_time
                    
                    return "\n".join(rich_text)
                else:
                    return None
                    
            except Exception as e:
                print(f"\n❌ Error during AssemblyAI transcription: {str(e)}\n")
                return None
                
        except Exception as e:
            print(f"\n❌ Error processing audio chunk: {str(e)}\n")
            return None
    
    def start_transcription(self):
        """Start the transcription process."""
        self.is_active = True
        self.audio_buffer.clear()
        # Set recording status for current target
        if self.target:
            self.recording_by_destination[self.target] = True
        # Clear transcription history when starting a new session
        transcription_history.clear()
        self.last_event_word_count = 0
        self.last_event_time = datetime.now() - timedelta(seconds=self.event_interval + 1)  # Allow immediate event
        print("🎙️ Audio transcription started - history cleared for new session")
    
    def stop_transcription(self):
        """Stop the transcription process."""
        self.is_active = False
        self.audio_buffer.clear()
        # Clear recording status for all destinations
        self.recording_by_destination.clear()
        print("🎙️ Audio transcription stopped")

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