import asyncio
import json
import websockets
from utils.logger import debug, warning, error, info
import time
from routes.lightsensor import broadcast_lux_level

DEBUGGING = False

# Registry for job progress listeners (used by generator.py)
job_progress_listeners = {}  # job_id: list of asyncio.Queue
job_progress_listeners_latest = {}  # job_id -> most recent update

# Store connected WebSocket clients
connected_clients = set()

# Handle WebSocket connections from overlay clients (receivers) and relays (senders)
async def handler(ws):
    if DEBUGGING: debug(f"🆕 WebSocket connection from: {ws.remote_address}")

    try:
        # Try to get the first message within a short time window
        try:
            first_msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            if DEBUGGING: debug(f"📥 First message from {ws.remote_address}: {first_msg}")

            try:
                data = json.loads(first_msg)
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
                if DEBUGGING: debug(f"✅ Broadcasted single message from {ws.remote_address}")

                # Now listen for more messages (e.g. RunPod relay)
                async for msg in ws:
                    if DEBUGGING: debug(f"📥 Message from {ws.remote_address}: {msg}")
                    try:
                        data = json.loads(msg)
                        # Handle lux sensor data if present
                        if "lux" in data:
                            sensor_name = data.get("sensor_name", "default")  # Use default if no name provided
                            await broadcast_lux_level(sensor_name, data["lux"])
                        job_id = data.get("job_id")
                        if job_id:
                            job_progress_listeners_latest[job_id] = data
                        await send_overlay_to_clients(data)
                    except Exception as e:
                        debug(f"⚠️ Could not parse message: {e}")
                        if DEBUGGING: debug(f"✉️ Offending message: {msg}")

            except Exception as e:
                debug(f"⚠️ Invalid JSON from sender: {e}")

        except asyncio.TimeoutError:
            # No message — this is a persistent overlay listener
            if DEBUGGING: debug(f"🖥️ Registered overlay client: {ws.remote_address}")
            connected_clients.add(ws)
            await ws.wait_closed()

    finally:
        connected_clients.discard(ws)
        debug(f"❌ Disconnected: {ws.remote_address}")

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
