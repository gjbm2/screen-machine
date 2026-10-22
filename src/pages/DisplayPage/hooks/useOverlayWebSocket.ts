// src/pages/DisplayPage/hooks/useOverlayWebSocket.ts
import { useEffect, useRef } from "react";

// Over HTTPS (the friendly LAN name) the overlay socket comes through Caddy at /ws/ (an insecure ws:// is blocked there
// and used to crash the page layer: black screen, owner 17 Oct); over http (the TVs, the public IP) VITE_WS_HOST as before.
const wsUrl = (): string =>
  typeof window !== "undefined" && window.location.protocol === "https:"
    ? `wss://${window.location.host}/ws/`
    : import.meta.env.VITE_WS_HOST;

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
  setOverlays: React.Dispatch<React.SetStateAction<Overlay[]>>,
  // Optional gate (controlled page only): return false to drop an overlay message, e.g. while
  // the screen is frozen or live (LIVE_V1 §2.2 rule 5). Without it, every message is shown as today.
  accept?: () => boolean,
) {
  const acceptRef = useRef(accept);
  acceptRef.current = accept;

  useEffect(() => {
    if (!screenId) return;

    let socket: WebSocket | null = null;
    let reconnectAttempts = 0;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    const connect = () => {
      const url = wsUrl();
      try {
        socket = new WebSocket(url);
      } catch (e) {
        // degrade, never refuse: no overlays, but the art stays up; try again later
        console.warn("🔌 overlay socket unavailable:", e);
        reconnectAttempts++;
        reconnectTimeout = setTimeout(connect, Math.min(30000, 1000 * 2 ** reconnectAttempts));
        return;
      }

      socket.onopen = () => {
        reconnectAttempts = 0;
        console.log("🟢 WebSocket connected to", url);
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
          if (acceptRef.current && !acceptRef.current()) return;

          const { html, duration, position, clear, fadein } = msg;
          const id = generateId();
          const showDuration = typeof duration === "number" ? duration : 5000;
          const displayTime = Math.max(0, showDuration);

          setOverlays((prev) => {
            const base = clear ? [] : [...prev];
            return [...base, {
              id,
              html,
              position,
              visible: fadein === 0,
              fadein
            }];
          });

          if (fadein !== 0) {
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
