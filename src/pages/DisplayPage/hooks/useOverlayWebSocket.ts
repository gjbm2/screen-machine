// src/pages/DisplayPage/hooks/useOverlayWebSocket.ts
import { useEffect, useRef } from "react";
import type { Overlay } from "../OverlayContainer";

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
  /** The caption of the picture being published with it (routes/publisher.py): shown once that picture is up. */
  with_base?: boolean;
}

/** How long overlays replaced by a `clear` take to fade out (they used to vanish in one frame). */
const CLEAR_FADEOUT_MS = 800;

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
  // Optional (controlled page only): runs `show` for a picture's caption once that picture is on glass
  // (art/baseLanding.ts). Without it, captions show on arrival as before.
  whenBaseLands?: (show: () => void) => void,
) {
  const acceptRef = useRef(accept);
  acceptRef.current = accept;
  const whenBaseLandsRef = useRef(whenBaseLands);
  whenBaseLandsRef.current = whenBaseLands;

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
        try {
          const msg: OverlayMessage = JSON.parse(event.data);
          // Only overlays carry html; the socket's other frames (lux, transcription, relayed comfy) are not for us.
          if (typeof msg.html !== "string" || !msg.html) return;
          console.log("📬 WS message received:", event.data);
          if (msg.screens && msg.screens.length > 0 && !msg.screens.includes(screenId)) {
            console.log(`🛑 Message not for this screen: ${screenId}`);
            return;
          }
          if (acceptRef.current && !acceptRef.current()) return;

          const { html, duration, position, clear, fadein } = msg;
          const withBase = !!msg.with_base;
          const displayTime = Math.max(0, typeof duration === "number" ? duration : 5000);

          const show = () => {
            const id = generateId();
            setOverlays((prev) => {
              // replaced overlays fade out under the new one instead of vanishing (a `fadeout` marks one dismissed:
              // one still waiting for its fade-in never comes up)
              const base = clear ? prev.map((o) => ({ ...o, visible: false, fadeout: o.fadeout ?? CLEAR_FADEOUT_MS })) : prev;
              return [...base, { id, html, position, visible: fadein === 0, fadein, withBase }];
            });

            if (fadein !== 0) {
              setTimeout(() => {
                setOverlays((prev) =>
                  prev.map((o) => (o.id === id && o.fadeout === undefined ? { ...o, visible: true } : o))
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
          };

          const defer = withBase ? whenBaseLandsRef.current : undefined;
          if (!defer) {
            show();
            return;
          }
          // its display time runs from when the picture is up; a freeze or live cut meanwhile drops it
          defer(() => {
            if (acceptRef.current && !acceptRef.current()) return;
            show();
          });
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
