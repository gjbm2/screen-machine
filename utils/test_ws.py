import asyncio
import json
import websockets

async def test_overlay():
    uri = "ws://localhost:8765"  # or whatever port your WebSocket server uses
    async with websockets.connect(uri) as websocket:
        message = {
            "screens": ["north-screen"],
            "htmlFile": "/overlays/overlay_alert.html",
            "duration": 3000,
            "position": "bottom-center",
            "clear": True
        }
        await websocket.send(json.dumps(message))
        print("✅ Test message sent to WebSocket server.")

asyncio.run(test_overlay())