import asyncio
import websockets
import sys
import json

async def test_connection():
    try:
        uri = "ws://localhost:8765"
        print(f"🔍 Attempting to connect to {uri}...")
        
        async with websockets.connect(uri) as websocket:
            message = {
                "type": "overlay",
                "data": {
                    "screens": ["devtest"],
                    "html": "overlay_explain.html.j2",  # This should be a path to your HTML template
                    "duration": 15000,
                    "position": "",
                    "substitutions": {
                        "MESSAGE": "Render › <span class=\"pulsing-stage\">Interpolate</span> › Upscale",
                        "PROGRESS_PERCENT": 50,
                        "BACKGROUND": "noneh"
                    },
                    "clear": True
                },
                "timestamp": 0,  # Will be set by server
                "id": "test"  # Will be set by server
            }
            
            print("📤 Sending message:", json.dumps(message, indent=2))
            await websocket.send(json.dumps(message))
            print("✅ Successfully sent message!")
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Could not connect to WebSocket server: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_connection()) 