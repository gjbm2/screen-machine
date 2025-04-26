import asyncio
import json
import websockets
import logging
from typing import Set, Dict, Any
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Registry for job progress listeners (used by generator.py)
job_progress_listeners = {}  # job_id: list of asyncio.Queue
job_progress_listeners_latest = {}  # job_id -> most recent update

# Store connected WebSocket clients by type
connected_clients: Dict[str, Set[websockets.WebSocketServerProtocol]] = {
    "overlay": set(),
    "animation": set(),
    "generation": set()
}

# Handle WebSocket connections
async def handler(ws: websockets.WebSocketServerProtocol, path: str):
    client_type = "overlay"  # Default type for backward compatibility
    logger.info(f"🆕 WebSocket connection from: {ws.remote_address}")
    
    try:
        async for message in ws:
            try:
                data = json.loads(message)
                
                # Handle subscription messages
                if isinstance(data, dict) and data.get("type") == "subscribe":
                    client_type = data.get("data", {}).get("type", "overlay")
                    if client_type not in connected_clients:
                        client_type = "overlay"  # Fallback for unknown types
                    connected_clients[client_type].add(ws)
                    logger.info(f"Client subscribed to {client_type} messages")
                    continue
                
                # Handle regular messages
                if isinstance(data, dict):
                    # Ensure message has required fields
                    if "type" not in data:
                        data["type"] = "overlay"  # Default type for backward compatibility
                    if "timestamp" not in data:
                        data["timestamp"] = int(datetime.now().timestamp())
                    if "id" not in data:
                        data["id"] = str(datetime.now().timestamp())
                
                # Send to appropriate clients
                await send_to_clients(data)
                
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received from {ws.remote_address}")
                continue
                
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Connection closed by {ws.remote_address}")
    finally:
        # Clean up client from all sets
        for clients in connected_clients.values():
            clients.discard(ws)

async def send_to_clients(data: Dict[str, Any]) -> None:
    """Send message to appropriate clients based on type"""
    message_type = data.get("type", "overlay")
    clients = connected_clients.get(message_type, set())
    
    if not clients:
        logger.warning(f"No clients connected for message type: {message_type}")
        return
        
    disconnected = set()
    for client in clients:
        try:
            await client.send(json.dumps(data))
        except websockets.exceptions.ConnectionClosed:
            disconnected.add(client)
        except Exception as e:
            logger.error(f"Error sending to client: {e}")
            disconnected.add(client)
    
    # Clean up disconnected clients
    for client in disconnected:
        clients.discard(client)

# WebSocket server entry point
async def start_ws_server():
    async with websockets.serve(handler, "0.0.0.0", 8765, compression=None):
        logger.info("🌐 WebSocket server running on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

# Launch WebSocket server
if __name__ == "__main__":
    asyncio.run(start_ws_server())
