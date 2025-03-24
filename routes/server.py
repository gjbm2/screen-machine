
import asyncio
import websockets
import sys
import time

async def client():
    # Use public IP and TCP port of the worker to communicate
    uri = "ws://213.173.99.31:31438"
    
    # Add retry mechanism
    max_retries = 5
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"Connecting to {uri} (attempt {retry_count + 1}/{max_retries})...")
            
            async with websockets.connect(uri, ping_interval=20, ping_timeout=30) as websocket:
                print("Connection established!")
                
                # Send a message
                await websocket.send("Hello")
                print("Message sent: Hello")
                
                # Wait for response
                response = await websocket.recv()
                print(f"Received: {response}")
                
                # Shutdown the server
                print("Sending shutdown command...")
                await websocket.send("shutdown")
                
                # Wait for confirmation
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    print(f"Shutdown response: {response}")
                except asyncio.TimeoutError:
                    print("No response to shutdown command (server may have closed)")
                
                return  # Success, exit function
                
        except (websockets.exceptions.ConnectionClosed, 
                websockets.exceptions.InvalidStatusCode,
                ConnectionRefusedError) as e:
            print(f"Connection error: {e}")
            retry_count += 1
            
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # Exponential backoff
                print(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("Maximum retries reached. Could not connect to the WebSocket server.")
                return
        except Exception as e:
            print(f"Unexpected error: {e}")
            return

if __name__ == "__main__":
    asyncio.run(client())
