import asyncio
import json
import websockets

import smconfig

async def test_overlay():
    uri = f"ws://localhost:{smconfig.port('overlay_ws')}"  # the deployment's overlay WebSocket
    async with websockets.connect(uri) as websocket:
        message = {
            "screens": smconfig.live_screens()[:1],
            "htmlFile": "/overlays/overlay_alert.html",
            "duration": 3000,
            "position": "bottom-center",
            "clear": True
        }
        await websocket.send(json.dumps(message))
        print("✅ Test message sent to WebSocket server.")

asyncio.run(test_overlay())