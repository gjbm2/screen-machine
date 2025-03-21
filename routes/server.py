import asyncio
import websockets

async def client():
    # Use public IP and TCP port of the worker to communicate
    uri = "ws://213.173.99.31:31438"
    async with websockets.connect(uri) as websocket:
        await websocket.send("Hello")
        response = await websocket.recv()
        print(f"Received: {response}")
        
        # shutdown the server
        await websocket.send("shutdown")
        response = await websocket.recv()
        print(f"Received: {response}")

asyncio.run(client())