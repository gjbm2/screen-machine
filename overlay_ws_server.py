import asyncio
import json
import websockets
from utils.logger import debug, warning, error, info
import time
from routes.lightsensor import broadcast_lux_level
from routes.audio_utils import get_audio_transcriber

DEBUGGING = False  # Keep original debugging off

# Registry for job progress listeners (used by generator.py)
job_progress_listeners = {}  # job_id: list of asyncio.Queue
job_progress_listeners_latest = {}  # job_id -> most recent update

# Store connected WebSocket clients
connected_clients = set()
audio_clients = set()  # Store audio clients separately

# Handle WebSocket connections from overlay clients (receivers) and relays (senders)
async def handler(websocket):
    is_audio_client = False
    transcriber = None

    try:
        # Try to get the first message within a short time window
        try:
            first_msg = await asyncio.wait_for(websocket.recv(), timeout=0.5)
            if DEBUGGING: debug(f"📥 First message from {websocket.remote_address}: {first_msg}")

            try:
                data = json.loads(first_msg)
                # Check if this is an audio client based on the message type
                if data.get("type") == "audio":
                    is_audio_client = True
                    audio_clients.add(websocket)
                    info("🎵 Audio stream started")
                    
                    # Initialize transcriber for this audio client
                    transcriber = get_audio_transcriber()
                    # Don't start transcription yet - wait for explicit start signal

                # Handle lux sensor data if present
                if "lux" in data:
                    sensor_name = data.get("sensor_name", "default")  # Use default if no name provided
                    await broadcast_lux_level(sensor_name, data["lux"])
                # Relay to overlay clients
                await send_overlay_to_clients(data)

                # 🔁 Also notify any job-specific listeners (e.g. generator.py)
                job_id = data.get("job_id")
                if job_id: 
                    job_progress_listeners_latest[job_id] = data  # 🧠 Store latest update
                    if job_id in job_progress_listeners:
                        for queue in job_progress_listeners[job_id]:
                            await queue.put(data)
                if DEBUGGING: debug(f"✅ Broadcasted single message from {websocket.remote_address}")

            except json.JSONDecodeError:
                if is_audio_client:
                    # For audio clients, this might be the first audio data chunk
                    if transcriber and isinstance(first_msg, bytes):
                        transcriber.add_audio_data(first_msg)
                else:
                    debug(f"⚠️ Invalid JSON from sender: {first_msg}")
                    return

        except asyncio.TimeoutError:
            # No message received within timeout - this is a persistent overlay listener
            if DEBUGGING: debug(f"🖥️ Registered overlay client: {websocket.remote_address}")
            connected_clients.add(websocket)
            try:
                await websocket.wait_closed()
            except websockets.exceptions.ConnectionClosed:
                pass
            return

        # Now listen for more messages
        async for msg in websocket:
            if is_audio_client:
                # For audio clients, check if this is a command or audio data
                if isinstance(msg, str):
                    try:
                        cmd = json.loads(msg)
                        if cmd.get("type") == "command":
                            command = cmd.get("command")
                            if command == "start_recording":
                                info("🎙️ Start recording command received")
                                if transcriber:
                                    transcriber.start_transcription()
                            elif command == "stop_recording":
                                info("🎙️ Stop recording command received")
                                if transcriber:
                                    transcriber.stop_transcription()
                                    # Remove from audio clients and close connection
                                    audio_clients.discard(websocket)
                                    await websocket.close(code=1000, reason="Stop recording requested")
                                    return
                    except json.JSONDecodeError:
                        debug(f"⚠️ Invalid command JSON: {msg}")
                elif isinstance(msg, bytes):
                    # This is raw audio data - pass directly to transcriber
                    if transcriber:
                        debug(f"🎙️ Received {len(msg)} bytes of audio data from client")
                        transcriber.add_audio_data(msg)
                    else:
                        debug("⚠️ Received audio data but no transcriber available")
            else:
                if DEBUGGING: debug(f"📥 Message from {websocket.remote_address}: {msg}")
                try:
                    data = json.loads(msg)
                    # Handle lux sensor data if present
                    if "lux" in data:
                        sensor_name = data.get("sensor_name", "default")
                        await broadcast_lux_level(sensor_name, data["lux"])
                    job_id = data.get("job_id")
                    if job_id:
                        job_progress_listeners_latest[job_id] = data
                    if job_id in job_progress_listeners:
                        for queue in job_progress_listeners[job_id]:
                            await queue.put(data)
                    await send_overlay_to_clients(data)
                except json.JSONDecodeError:
                    debug(f"⚠️ Invalid JSON from {websocket.remote_address}: {msg}")

    except websockets.exceptions.ConnectionClosed:
        debug(f"❌ Connection closed: {websocket.remote_address}")
    except Exception as e:
        error(f"❌ Error handling WebSocket connection: {e}")
    finally:
        if is_audio_client:
            audio_clients.discard(websocket)
            info("🎵 Audio stream ended")
            
            # Stop transcription for this client
            if transcriber:
                transcriber.stop_transcription()
        else:
            connected_clients.discard(websocket)
            if DEBUGGING: debug(f"❌ Disconnected: {websocket.remote_address}")

# Send overlay message to all connected clients
async def send_overlay_to_clients(data: dict):
    if DEBUGGING: debug("📤 Preparing to broadcast overlay:", data)

    if not connected_clients:
        debug("⚠️ No connected clients to send to.")
        return

    msg = json.dumps(data)
    if DEBUGGING: debug(f"📦 Broadcasting message: {msg}")

    try:
        await asyncio.gather(*(ws.send(msg) for ws in connected_clients))
        if DEBUGGING: debug(f"✅ Sent overlay to {len(connected_clients)} client(s).")
    except Exception as e:
        debug(f"❌ Error sending message to clients: {e}")

# WebSocket server entry point
async def ws_main():
    async with websockets.serve(handler, "0.0.0.0", 8765, compression=None):
        debug("🌐 WebSocket server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

# Launch WebSocket server
def start_ws_server():
    asyncio.run(ws_main())

if __name__ == "__main__":
    start_ws_server()
