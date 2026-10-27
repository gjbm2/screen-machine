import asyncio
import json
import logging
import websockets
from datetime import datetime, timedelta

from utils.logger import debug, warning, error, info
from routes.lightsensor import broadcast_lux_level
from routes.audio_utils import get_audio_transcriber
from connection_registry import registry  # NEW central registry
from routes.jobs import get_tracker

DEBUGGING = False  # Keep original debugging off

# websockets will log "opening handshake failed" at ERROR with a full traceback
# whenever a non-WebSocket client (TCP port check / health check / scanner)
# connects and disconnects without sending a proper HTTP Upgrade request.
#
# This isn't a server bug; it's just noise. Keep the log line but demote it and
# drop the traceback so real WS issues remain visible.
_WS_LOGGER = logging.getLogger("screen_machine.websockets")
_WS_LOGGER.setLevel(logging.INFO)


class _WebsocketsHandshakeNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True

        if "opening handshake failed" in msg:
            record.levelno = logging.INFO
            record.levelname = "INFO"
            record.exc_info = None
            record.stack_info = None
        return True


_WS_LOGGER.addFilter(_WebsocketsHandshakeNoiseFilter())

# Clients render these fields as HTML. External senders (RunPod worker sends
# {job_id, comfy}; light relay sends {lux, sensor_name}) never set them, so
# stripping them from relayed frames stops anyone who can reach this port from
# injecting markup/script into the kiosks and operator UI. Server-originated
# overlays go through send_overlay_to_clients directly and are unaffected.
_RELAY_STRIPPED_KEYS = ("html", "substitutions")


# The operator UI turns 'generation_update' frames into gallery entries; no
# backend code emits that type, so a relayed one can only be spoofed.
_RELAY_BLOCKED_TYPES = ("generation_update",)


def _sanitize_relayed(data):
    if not isinstance(data, dict):
        return data
    blocked_type = data.get("type") in _RELAY_BLOCKED_TYPES
    if blocked_type or any(k in data for k in _RELAY_STRIPPED_KEYS):
        warning(f"Stripped HTML/blocked fields from relayed WS message (keys: {sorted(data.keys())})")
        return {k: v for k, v in data.items()
                if k not in _RELAY_STRIPPED_KEYS and not (k == "type" and blocked_type)}
    return data


async def _handle_sender_frame(data):
    """One JSON frame from a sending socket (light relay, RunPod worker, manual tools)."""
    if not isinstance(data, dict):
        return
    data = _sanitize_relayed(data)
    # Handle lux sensor data if present
    if "lux" in data:
        sensor_name = data.get("sensor_name", "default")  # Use default if no name provided
        await broadcast_lux_level(sensor_name, data["lux"])
    # The RunPod worker's ComfyUI relay ({job_id: <RunPod id>, comfy}) is job progress: it goes to the
    # job tracker (docs/JOBS.md), which pages subscribe to; screens no longer get it
    if data.get("job_id") and "comfy" in data:
        try:
            get_tracker().on_worker_frame(data["job_id"], data["comfy"])
        except Exception as e:
            error(f"Job tracker rejected a worker frame: {e}")
        return
    # Relay to overlay clients
    await send_overlay_to_clients(data)

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

                await _handle_sender_frame(data)
                if DEBUGGING: debug(f"✅ Handled first message from {websocket.remote_address}")

            except json.JSONDecodeError:
                if is_audio_client:
                    # For audio clients, this might be the first audio data chunk
                    if transcriber and isinstance(first_msg, bytes):
                        transcriber.add_audio_data(first_msg)
                else:
                    debug(f"⚠️ Invalid JSON from sender: {first_msg}")
                    return

        except asyncio.TimeoutError:
            # Nothing within the window: a viewer (screen, operator page), so it gets broadcasts. Its
            # later frames are still read below as sender frames, because a relay that is slow to
            # send its first frame (the RunPod worker often is) must not be ignored
            if DEBUGGING: debug(f"🖥️ Registered overlay client: {websocket.remote_address}")
            registry.add_overlay(websocket)

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
                    await _handle_sender_frame(json.loads(msg))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    debug(f"⚠️ Invalid JSON from {websocket.remote_address}")

    except websockets.exceptions.ConnectionClosed as e:
        info(f"🔌 Connection closed (code={e.code} reason={e.reason})")
    except Exception as e:
        error(f"❌ Error in WebSocket handler: {e}")
    finally:
        # Cleanup on disconnection
        if ping_task:
            ping_task.cancel()

        # Closed viewers were only dropped when a later broadcast failed on them
        registry.remove_overlay(websocket)

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
    async with websockets.serve(
        handler,
        "0.0.0.0",
        8765,
        compression=None,
        logger=_WS_LOGGER,
    ):
        debug("🌐 WebSocket server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

# Launch WebSocket server
def start_ws_server():
    asyncio.run(ws_main())

if __name__ == "__main__":
    start_ws_server()
