
from websocket_server import WebsocketServer
import runpod
import os
import signal
import sys
import time

shutdown_flag = False  # Flag to track when to stop the server

def on_new_client(client, server):
    """Handle new client connection."""
    print(f"New client connected: {client['id']}")
    server.send_message(client, "Connection established successfully")

def on_message(client, server, message):
    """Handle incoming messages from clients."""
    global shutdown_flag

    print(f"Received message: {message}")
    
    # Echo the message back to client
    server.send_message(client, f"Echo: {message}")

    # If the client sends "shutdown", set flag and close server
    if message.strip().lower() == "shutdown":
        print("Shutdown command received. Stopping WebSocket server...")
        server.send_message(client, "Server shutting down...")
        shutdown_flag = True
        server.shutdown_gracefully()  # Use graceful shutdown if available

def on_client_left(client, server):
    """Handle client disconnection."""
    print(f"Client {client['id']} disconnected")

def signal_handler(sig, frame):
    """Handle system signals for graceful shutdown."""
    global shutdown_flag
    print("Received shutdown signal, stopping server...")
    shutdown_flag = True

def start_websocket(port=8765):
    """Start WebSocket server and wait for shutdown."""
    global shutdown_flag
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create server with explicit host and port
    try:
        server = WebsocketServer(host="0.0.0.0", port=port)
        server.set_fn_new_client(on_new_client)
        server.set_fn_client_left(on_client_left)
        server.set_fn_message_received(on_message)

        print(f"WebSocket server starting on port {port}...")
        
        # Start the server
        server_thread = server.run_forever_threaded()
        
        # Keep the main thread alive until shutdown is requested
        while not shutdown_flag:
            time.sleep(1)
            
        # Clean shutdown
        print("Shutting down WebSocket server...")
        server.shutdown_gracefully() if hasattr(server, 'shutdown_gracefully') else server.shutdown()
        
    except Exception as e:
        print(f"Error in WebSocket server: {e}")
        return f"WebSocket server error: {e}"

    return "WebSocket server stopped successfully"

def handler(event):
    """RunPod job handler that runs the WebSocket server and waits for shutdown."""

    public_ip = os.environ.get('RUNPOD_PUBLIC_IP', 'localhost')  
    tcp_port = int(os.environ.get('RUNPOD_TCP_PORT_8765', '8765')) 
    
    print(f"Public IP: {public_ip}")  
    print(f"TCP Port: {tcp_port}")  

    runpod.serverless.progress_update(event, f"WebSocket server starting - Public IP: {public_ip}, TCP Port: {tcp_port}")

    # Start WebSocket server and wait for shutdown message
    result = start_websocket(port=tcp_port)

    return {
        "message": result,
        "public_ip": public_ip,
        "tcp_port": tcp_port
    }

if __name__ == '__main__':
    runpod.serverless.start({'handler': handler})
