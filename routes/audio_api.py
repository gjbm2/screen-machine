from flask import Blueprint, jsonify
from routes.audio_utils import get_audio_transcriber, transcription_history

# Create blueprint
audio_api = Blueprint('audio_api', __name__)

@audio_api.route('/transcription-history', methods=['GET'])
def get_transcription_history():
    """Get the last 1000 words of transcription history."""
    return jsonify({
        'transcription': ' '.join(transcription_history)
    })

@audio_api.route('/transcription-status', methods=['GET'])
def get_transcription_status():
    """Get current transcription status including active clients and state."""
    transcriber = get_audio_transcriber()
    return jsonify({
        'is_active': transcriber.is_active,  # Transcription active
        'is_recording': transcriber.get_recording_status(),  # Recording status by destination
        'client_ids': list(transcriber.client_ids),
        'target': transcriber.target,
        'current_transcription': ' '.join(transcription_history),
        'last_event_time': transcriber.last_event_time.isoformat() if transcriber.last_event_time else None,
        'last_event_word_count': transcriber.last_event_word_count
    })

@audio_api.route('/force-clear-clients', methods=['POST'])
def force_clear_clients():
    """Force clear all audio clients and stop transcription."""
    transcriber = get_audio_transcriber()
    
    # Clear transcriber clients
    client_count = len(transcriber.client_ids)
    transcriber.client_ids.clear()
    
    # Stop transcription
    transcriber.stop_transcription()
    
    # Clear recording status
    transcriber.recording_by_destination.clear()
    
    return jsonify({
        'status': 'cleared',
        'clients_cleared': client_count,
        'message': f'Cleared {client_count} clients and stopped transcription'
    }) 