// src/pages/DisplayPage/hooks/useOverlayWebSocket.ts
import { useEffect } from "react";

const WS_HOST = import.meta.env.VITE_WS_HOST;

interface OverlayMessage {
  html: string;
  duration: number;
  position?: string;
  clear?: boolean;
  screens?: string[];
  fadein?: number;
}

interface Overlay {
  id: string;
  html: string;
  position: string | undefined;
  visible: boolean;
  fadein?: number;
}

function generateId(): string {
  return (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : Math.random().toString(36).substring(2, 10);
}

export default function useOverlayWebSocket(
  screenId: string | undefined,
  setOverlays: React.Dispatch<React.SetStateAction<Overlay[]>>
) {
  useEffect(() => {
    if (!screenId) return;

    let socket: WebSocket | null = null;
    let reconnectAttempts = 0;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    const connect = () => {
      socket = new WebSocket(WS_HOST);

      socket.onopen = () => {
        reconnectAttempts = 0;
        console.log("🟢 WebSocket connected to", WS_HOST);
      };

      socket.onclose = () => {
        console.warn("🔌 WebSocket closed, attempting to reconnect...");
        reconnectAttempts++;
        const delay = Math.min(10000, 1000 * 2 ** reconnectAttempts);
        reconnectTimeout = setTimeout(connect, delay);
      };

      socket.onerror = (err) => {
        console.error("🔴 WebSocket error:", err);
        socket?.close();
      };

      socket.onmessage = (event) => {
        console.log("📬 WS message received:", event.data);
        try {
          const msg: OverlayMessage = JSON.parse(event.data);
          if (msg.screens && msg.screens.length > 0 && !msg.screens.includes(screenId)) {
            console.log(`🛑 Message not for this screen: ${screenId}`);
            return;
          }

          const { html, duration, position, clear } = msg;
          const id = generateId();
          const showDuration = typeof duration === "number" ? duration : 5000;
          const displayTime = Math.max(0, showDuration);

          setOverlays((prev) => {
            const base = clear ? [] : [...prev];
            return [...base, {
              id,
              html,
              position,
              visible: msg.fadein === 0,
              fadein: msg.fadein
            }];
          });

          if (msg.fadein !== 0) {
            setTimeout(() => {
              setOverlays((prev) =>
                prev.map((o) => (o.id === id ? { ...o, visible: true } : o))
              );
            }, 50);
          }

          setTimeout(() => {
            setOverlays((prev) =>
              prev.map((o) => (o.id === id ? { ...o, visible: false } : o))
            );

            setTimeout(() => {
              setOverlays((prev) => prev.filter((o) => o.id !== id));
            }, 5000);
          }, displayTime);
        } catch (err) {
          console.error("Overlay message failed:", err);
        }
      };
    };

    connect();

    return () => {
      socket?.close();
      clearTimeout(reconnectTimeout);
    };
  }, [screenId, setOverlays]);
}
