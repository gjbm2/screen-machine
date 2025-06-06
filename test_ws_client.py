# server.py — run on 85.254.136.253
import asyncio
import websockets

async def handler(websocket):
    print("Relay connected")
    try:
        async for message in websocket:
            print("Received:", message)
    except websockets.exceptions.ConnectionClosed:
        print("Relay disconnected")

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("Listening on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

asyncio.run(main())
