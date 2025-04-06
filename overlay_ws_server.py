# overlay_ws_server.py

import asyncio
import json
import websockets

# Store connected WebSocket clients
connected_clients = set()

# Handle incoming WebSocket connections
async def handler(ws):
    print(f"🆕 WebSocket client connected: {ws.remote_address}")
    connected_clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        connected_clients.remove(ws)
        print(f"❌ WebSocket client disconnected: {ws.remote_address}")

# Send overlay message to all connected clients
async def send_overlay_to_clients(data: dict):
    print("📤 Preparing to broadcast overlay:", data)

    if not connected_clients:
        print("⚠️ No connected clients to send to.")
        return

    msg = json.dumps(data)
    print(f"📦 Broadcasting message: {msg}")

    try:
        await asyncio.gather(*(ws.send(msg) for ws in connected_clients))
        print(f"✅ Sent overlay to {len(connected_clients)} client(s).")
    except Exception as e:
        print(f"❌ Error sending message to clients: {e}")

# WebSocket server entry point
async def ws_main():
    async with websockets.serve(handler, "0.0.0.0", 8765, compression=None):
        print("🌐 WebSocket server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

# Launch WebSocket server from a thread or main
def start_ws_server():
    asyncio.run(ws_main())

# Allow standalone use
if __name__ == "__main__":
  
    start_ws_server()
    