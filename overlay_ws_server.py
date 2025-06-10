import asyncio
import json
import websockets
from datetime import datetime, timedelta

from utils.logger import debug, warning, error, info
from routes.lightsensor import broadcast_lux_level
from routes.audio_utils import get_audio_transcriber
from connection_registry import registry  # NEW central registry

DEBUGGING = False  # Keep original debugging off

# Registry for job progress listeners (used by generator.py)
job_progress_listeners = {}  # job_id: list of asyncio.Queue
job_progress_listeners_latest = {}  # job_id -> most recent update

# No per-client audio set needed (handled by registry)

# OLD globals replaced by registry in connection_registry.py

# Ping configuration
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds

# Add with other global variables
last_stop_time = {}  # Track last stop time by target

# Lightweight keep-alive that just sends a ping every 20 s so that
# the event-loop touches the socket and client pings are answered.
async def passive_keepalive(ws):
    try:
        while True:
            await asyncio.sleep(20)
            try:
                pong_waiter = await ws.ping()
                # wait but ignore timeout – client will decide
                await asyncio.wait_for(pong_waiter, timeout=5)
            except Exception:
                # Any error means the socket is likely closed; let outer handler deal
                break
    except asyncio.CancelledError:
        pass

async def ping_client(websocket, client_id=None):
    """Send periodic pings to keep connection alive and detect stale connections."""
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                pong_waiter = await websocket.ping()
                await asyncio.wait_for(pong_waiter, timeout=PING_TIMEOUT)
                if DEBUGGING: debug(f"✅ Ping successful for client {client_id or websocket.remote_address}")
            except asyncio.TimeoutError:
                error(f"❌ Ping timeout for client {client_id or websocket.remote_address}")
                await websocket.close(code=1000, reason="Ping timeout")
                break
            except websockets.exceptions.ConnectionClosed:
                break
    except asyncio.CancelledError:
        pass  # Task was cancelled, exit gracefully

# Handle WebSocket connections from overlay clients (receivers) and relays (senders)
async def handler(websocket):
    is_audio_client = False
    transcriber = None
    client_id = None
    ping_task = None
    target = "global"
    streaming_enabled = False  # Only true between start_recording / stop_recording
    keepalive_task = None

    try:
        # Start ping task
        ping_task = asyncio.create_task(ping_client(websocket))

        # Try to get the first message within a short time window
        try:
            first_msg = await asyncio.wait_for(websocket.recv(), timeout=0.5)
            if DEBUGGING: debug(f"📥 First message from {websocket.remote_address}: {first_msg}")

            try:
                data = json.loads(first_msg)
                # Check if this is an audio client based on the message type
                if data.get("type") == "audio":
                    target = data.get("target", "global")
                    # Check if we're in cooldown for this target
                    if target in last_stop_time and datetime.now() - last_stop_time[target] < timedelta(seconds=2):
                        info(f"🎙️ Rejecting reconnection for target {target} - in cooldown")
                        await websocket.close(code=1000, reason="Cooldown period")
                        return
                        
                    is_audio_client = True
                    registry.attach_audio(target, websocket)
                    info("🎵 Audio websocket connected (awaiting start_recording)")
                    
                    # Lazily create transcriber on first start
                    if not transcriber:
                        transcriber = get_audio_transcriber()
                    transcriber.target = target
                    transcriber.start_transcription()

                    # Audio sockets send data frequently; no need for ping watchdog
                    if ping_task:
                        ping_task.cancel()

                    # Start passive keep-alive so we respond to client pings
                    keepalive_task = asyncio.create_task(passive_keepalive(websocket))

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
            registry.add_overlay(websocket)
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
                                streaming_enabled = True
                                if transcriber:
                                    transcriber.target = target  # ensure up to date
                                    transcriber.start_transcription()
                            elif command == "stop_recording":
                                info("🎙️ Stop recording command received")
                                streaming_enabled = False

                                # Gracefully stop transcription for THIS client/target only
                                if transcriber:
                                    transcriber.stop_transcription()
                                    info("🎙️ Transcription stopped (client keeps connection open)")

                                # Record stop time to enforce short cooldown against instant reconnect
                                last_stop_time[target] = datetime.now()

                                # Socket remains open; client may start again without reconnecting
                    except json.JSONDecodeError:
                        # If not JSON, treat as audio data
                        if streaming_enabled and transcriber and isinstance(msg, bytes):
                            transcriber.add_audio_data(msg)
                else:
                    # Binary message - treat as audio data only if streaming enabled
                    if streaming_enabled and transcriber and isinstance(msg, bytes):
                        transcriber.add_audio_data(msg)
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

    except websockets.exceptions.ConnectionClosed as e:
        info(f"🔌 Connection closed (code={e.code} reason={e.reason})")
    except Exception as e:
        error(f"❌ Error in WebSocket handler: {e}")
    finally:
        # Cleanup on disconnection
        if ping_task:
            ping_task.cancel()
        
        if is_audio_client:
            registry.detach_audio(target, websocket)
            info("🎵 Audio stream ended for audio websocket")
            if transcriber:
                transcriber.stop_transcription()
        
        # No stale-audio cleanup needed; registry tracks live sockets automatically
        if keepalive_task:
            keepalive_task.cancel()

# Send overlay message to all connected clients
async def send_overlay_to_clients(data: dict):
    if DEBUGGING: debug("📤 Preparing to broadcast overlay:", data)

    # Add specific logging for transcription messages
    if data.get("type") == "transcription":
        info(f"🎙️ Broadcasting transcription: target='{data.get('target')}'")

    if not registry.overlays:
        debug("⚠️ No connected overlay clients.")
        return

    msg = json.dumps(data)
    if DEBUGGING: debug(f"📦 Broadcasting message: {msg}")

    # Send to each client individually and handle disconnections
    disconnected_clients = []
    successful_sends = 0
    
    for ws in list(registry.overlays):  # Iterate over a snapshot
        try:
            await ws.send(msg)
            successful_sends += 1
        except websockets.exceptions.ConnectionClosed:
            # Client disconnected, mark for removal
            disconnected_clients.append(ws)
            debug(f"🔌 Client {ws.remote_address} disconnected, removing from list")
        except Exception as e:
            # Other error, also mark for removal
            disconnected_clients.append(ws)
            debug(f"❌ Error sending to client {ws.remote_address}: {e}")
    
    # Remove disconnected clients
    for ws in disconnected_clients:
        registry.remove_overlay(ws)
    
    # Log results
    if data.get("type") == "transcription":
        if successful_sends > 0:
            info(f"✅ Successfully sent transcription to {successful_sends} client(s)")
        if disconnected_clients:
            info(f"🧹 Removed {len(disconnected_clients)} disconnected clients")
    
    # Log if we removed any clients
    if disconnected_clients and DEBUGGING:
        debug(f"🧹 Cleaned up {len(disconnected_clients)} disconnected clients")

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
