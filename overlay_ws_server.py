import asyncio
import json
import websockets

DEBUGGING = False

# Registry for job progress listeners (used by generator.py)
job_progress_listeners = {}  # job_id: list of asyncio.Queue
job_progress_listeners_latest = {}  # job_id -> most recent update

# Store connected WebSocket clients
connected_clients = set()

# Handle WebSocket connections from overlay clients (receivers) and relays (senders)
async def handler(ws):
    if DEBUGGING: print(f"🆕 WebSocket connection from: {ws.remote_address}")

    try:
        # Try to get the first message within a short time window
        try:
            first_msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            if DEBUGGING: print(f"📥 First message from {ws.remote_address}: {first_msg}")

            # Sender — could be send_overlay or RunPod
            try:
                data = json.loads(first_msg)                
                # Relay to overlay clients
                await send_overlay_to_clients(data)

                # 🔁 Also notify any job-specific listeners (e.g. generator.py)
                job_id = data.get("job_id")
                if job_id: 
                    job_progress_listeners_latest[job_id] = data  # 🧠 Store latest update
                    if job_id in job_progress_listeners:
                        for queue in job_progress_listeners[job_id]:
                            await queue.put(data)
                
                if DEBUGGING: print(f"✅ Broadcasted single message from {ws.remote_address}")

                # Now listen for more messages (e.g. RunPod relay)
                async for msg in ws:
                    if DEBUGGING: print(f"📥 Message from {ws.remote_address}: {msg}")
                    try:
                        data = json.loads(msg)
                        job_id = data.get("job_id")
                        if job_id:
                            job_progress_listeners_latest[job_id] = data
                        await send_overlay_to_clients(data)
                    except Exception as e:
                        print(f"⚠️ Could not parse message: {e}")
                        if DEBUGGING: print(f"✉️ Offending message: {msg}")

            except Exception as e:
                print(f"⚠️ Invalid JSON from sender: {e}")

        except asyncio.TimeoutError:
            # No message — this is a persistent overlay listener
            if DEBUGGING: print(f"🖥️ Registered overlay client: {ws.remote_address}")
            connected_clients.add(ws)
            await ws.wait_closed()

    finally:
        connected_clients.discard(ws)
        print(f"❌ Disconnected: {ws.remote_address}")

# Send overlay message to all connected clients
async def send_overlay_to_clients(data: dict):
    if DEBUGGING: print("📤 Preparing to broadcast overlay:", data)

    if not connected_clients:
        print("⚠️ No connected clients to send to.")
        return

    msg = json.dumps(data)
    if DEBUGGING: print(f"📦 Broadcasting message: {msg}")

    try:
        await asyncio.gather(*(ws.send(msg) for ws in connected_clients))
        if DEBUGGING: print(f"✅ Sent overlay to {len(connected_clients)} client(s).")
    except Exception as e:
        print(f"❌ Error sending message to clients: {e}")

# WebSocket server entry point
async def ws_main():
    async with websockets.serve(handler, "0.0.0.0", 8765, compression=None):
        print("🌐 WebSocket server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

# Launch WebSocket server
def start_ws_server():
    asyncio.run(ws_main())

if __name__ == "__main__":
    start_ws_server()
