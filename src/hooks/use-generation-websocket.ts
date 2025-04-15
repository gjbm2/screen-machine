
import { useEffect, useState } from 'react';
import { WebSocketMessage } from './image-generation/types';
import { nanoid } from '@/lib/utils';
import { toast } from 'sonner';

// Get WebSocket host from environment variables
const WS_HOST = import.meta.env.VITE_WS_HOST || 'wss://your-default-ws-host.com';

export function useGenerationWebSocket(
  onMessage?: (message: WebSocketMessage) => void,
  sessionId: string = nanoid()
) {
  const [connected, setConnected] = useState(false);
  const [socket, setSocket] = useState<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectAttempts = 0;
    let reconnectTimeout: ReturnType<typeof setTimeout>;
    let unmounted = false;

    const connect = () => {
      if (unmounted) return;
      
      // Close existing socket if it exists
      if (ws) {
        ws.close();
      }

      try {
        ws = new WebSocket(WS_HOST);
        setSocket(ws);

        ws.onopen = () => {
          console.log("🟢 Generation WebSocket connected to", WS_HOST);
          setConnected(true);
          reconnectAttempts = 0;
          
          // Send session identification
          ws.send(JSON.stringify({ 
            type: 'identify', 
            session_id: sessionId 
          }));
        };

        ws.onclose = (event) => {
          console.warn("🔌 Generation WebSocket closed, attempting to reconnect...", event.code, event.reason);
          setConnected(false);
          
          if (unmounted) return;
          
          reconnectAttempts++;
          const delay = Math.min(10000, 1000 * 2 ** reconnectAttempts);
          reconnectTimeout = setTimeout(connect, delay);
        };

        ws.onerror = (err) => {
          console.error("🔴 Generation WebSocket error:", err);
          ws?.close();
        };

        ws.onmessage = (event) => {
          try {
            console.log("📬 Generation WS message received:", event.data);
            const message = JSON.parse(event.data) as WebSocketMessage;
            
            // For now, simply pass the message to the callback
            // Later, we'll implement specific handling for different message types
            if (onMessage) {
              onMessage(message);
            }

            // Show toasts for important updates
            if (message.type === 'generation_update') {
              if (message.status === 'completed') {
                toast.success(`Generation completed for batch ${message.batch_id.slice(0, 8)}`);
              } else if (message.status === 'error') {
                toast.error(`Generation failed: ${message.error || 'Unknown error'}`);
              }
            }
          } catch (err) {
            console.error("Failed to process WebSocket message:", err);
          }
        };
      } catch (error) {
        console.error("Error establishing WebSocket connection:", error);
        
        if (!unmounted) {
          reconnectAttempts++;
          const delay = Math.min(10000, 1000 * 2 ** reconnectAttempts);
          reconnectTimeout = setTimeout(connect, delay);
        }
      }
    };

    connect();

    return () => {
      unmounted = true;
      clearTimeout(reconnectTimeout);
      if (ws) {
        ws.close();
      }
    };
  }, [sessionId, onMessage]);

  const sendMessage = (message: any) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  return { connected, sendMessage };
}
