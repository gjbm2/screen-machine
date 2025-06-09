import io
import wave
import os
from utils.logger import debug, info, error, warning
import assemblyai as aai
from datetime import datetime
import numpy as np
from routes.audio_api import transcription_history
from flask import Blueprint, request

# Create blueprint
audio_bp = Blueprint('audio', __name__)

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_DURATION = 5  # seconds per chunk for AssemblyAI

class AudioTranscriber:
    def __init__(self):
        # Set up AssemblyAI
        api_key = os.getenv('ASSEMBLY_AI_KEY')
        if not api_key:
            error("ASSEMBLY_AI_KEY environment variable not set")
            raise ValueError("ASSEMBLY_AI_KEY environment variable not set")
        
        aai.settings.api_key = api_key
        self.is_active = False
        self.audio_buffer = bytearray()
        self.bytes_per_chunk = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS * 5  # 5 seconds of audio
        self.min_chunk_size = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS * 2  # 2 seconds minimum
        info("🎙️ Audio transcriber initialized")
    
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
                # Create transcription config with correct parameters
                config = aai.TranscriptionConfig(
                    language_code="en"
                )
                
                transcript = transcriber.transcribe(wav_buffer, config=config)
                
                # Only log the transcription text and add to history
                if transcript and transcript.text:
                    print(f"\n🎙️ Transcription: {transcript.text}\n")
                    # Add words to history
                    transcription_history.extend(transcript.text.split())
                    return transcript.text
                else:
                    print("\n🎙️ No transcription text received\n")
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
        print("🎙️ Audio transcription started")
    
    def stop_transcription(self):
        """Stop the transcription process."""
        self.is_active = False
        self.audio_buffer.clear()
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
    transcriber.start_transcription()
    return {'status': 'started'}

@audio_bp.route('/stop-transcription', methods=['POST'])
def stop_transcription():
    """Stop the audio transcription process."""
    transcriber.stop_transcription()
    return {'status': 'stopped'}

@audio_bp.route('/audio', methods=['POST'])
def handle_audio():
    """Handle incoming audio data."""
    audio_data = request.get_data()
    if audio_data:
        transcriber.add_audio_data(audio_data)
    return {'status': 'received'} 