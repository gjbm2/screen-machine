from flask import Blueprint, jsonify
from typing import List, Deque
from collections import deque

# Create a deque to store the last 1000 words of transcription
transcription_history: Deque[str] = deque(maxlen=1000)

# Create blueprint
audio_api = Blueprint('audio_api', __name__)

@audio_api.route('/transcription-history', methods=['GET'])
def get_transcription_history():
    """Get the last 1000 words of transcription history."""
    return jsonify({
        'transcription': ' '.join(transcription_history)
    }) 