import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { getPublishDestinations } from "@/services/PublishService";

const POLL_INTERVAL = 2000;
const FADE_DURATION = 5000;
const OVERLAY_FADEIN_DURATION = 2000;
const OVERLAY_FADEOUT_DURATION = 5000;
const WS_HOST = "ws://172.28.255.46:8765";

interface OverlayMessage {
  html: string;
  duration: number;
  position: string;
  clear?: boolean;
  screens?: string[];
}

interface Overlay {
  id: string;
  html: string;
  position: string;
  visible: boolean;
}

function generateId(): string {
  return (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : Math.random().toString(36).substring(2, 10);
}

function useOverlayWebSocket(
  screenId: string | undefined,
  setOverlays: React.Dispatch<React.SetStateAction<Overlay[]>>
) {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectAttempts = 0;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    const connect = () => {
      socket = new WebSocket(WS_HOST);

      socket.onopen = () => {
        reconnectAttempts = 0;
        console.log("🟢 WebSocket connected");
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
          if (msg.screens && msg.screens.length > 0 && !msg.screens.includes(screenId || "")) {
            console.log(`🛑 Message not for this screen: ${screenId}`);
            return;
          }

          const { html, duration, position, clear } = msg;
          const id = generateId();
          const showDuration = typeof duration === "number" ? duration : 5000;
          const displayTime = Math.max(0, showDuration);

          setOverlays((prev) => {
            const base = clear ? [] : [...prev];
            return [...base, { id, html, position, visible: false }];
          });

          setTimeout(() => {
            setOverlays((prev) =>
              prev.map((o) => (o.id === id ? { ...o, visible: true } : o))
            );
          }, 50);

			setTimeout(() => {
			  setOverlays((prev) =>
				prev.map((o) => (o.id === id ? { ...o, visible: false } : o))
			  );

			  // Remove only *after* the fade-out transition completes
			  const removalTimeout = setTimeout(() => {
				setOverlays((prev) => prev.filter((o) => o.id !== id));
			  }, OVERLAY_FADEOUT_DURATION);
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

function getOverlayStyle(position: string, visible: boolean): React.CSSProperties {
  const baseStyle: React.CSSProperties = {
    position: "absolute",
    zIndex: 10000,
    opacity: visible ? 1 : 0,
    transition: `opacity ${visible ? OVERLAY_FADEIN_DURATION : OVERLAY_FADEOUT_DURATION}ms ease`,
    pointerEvents: "none"
  };

  switch (position) {
    case "top-left":
      return { ...baseStyle, top: "20px", left: "20px" };
    case "top-center":
      return { ...baseStyle, top: "20px", left: "50%", transform: "translateX(-50%)" };
    case "top-right":
      return { ...baseStyle, top: "20px", right: "20px" };
    case "bottom-left":
      return { ...baseStyle, bottom: "20px", left: "20px" };
    case "bottom-center":
      return { ...baseStyle, bottom: "20px", left: "50%", transform: "translateX(-50%)" };
    case "bottom-right":
      return { ...baseStyle, bottom: "20px", right: "20px" };
    default:
      return { ...baseStyle, bottom: "20px", left: "50%", transform: "translateX(-50%)" };
  }
}

export default function DisplayPage() {
  const { screenId } = useParams();
  const [currentSrc, setCurrentSrc] = useState<string | null>(null);
  const [fadeInSrc, setFadeInSrc] = useState<string | null>(null);
  const [fadeInVisible, setFadeInVisible] = useState(false);
  const [overlays, setOverlays] = useState<Overlay[]>([]);

  const lastModifiedRef = useRef<number>(0);
  const imageFileRef = useRef<string | null>(null);

  useEffect(() => {
    const destinations = getPublishDestinations();
    const matched = destinations.find(
      (d) => d.id === screenId && d.type === "output_file"
    );
    if (matched) {
      imageFileRef.current = matched.file;
      const initialUrl = `/output/${matched.file}?t=${Date.now()}`;
      lastModifiedRef.current = Date.now();
      setCurrentSrc(initialUrl);
    }
  }, [screenId]);

  useEffect(() => {
    if (!imageFileRef.current) return;
    const imageUrl = `/output/${imageFileRef.current}`;

    const checkForChange = async () => {
      try {
        const res = await fetch(imageUrl, { method: "HEAD" });
        const lastModified = new Date(res.headers.get("last-modified") || 0).getTime();
        if (lastModified > lastModifiedRef.current) {
          lastModifiedRef.current = lastModified;
          const newUrl = `${imageUrl}?t=${Date.now()}`;
          const img = new Image();
          img.src = newUrl;
          img.onload = () => {
            setFadeInSrc(newUrl);
            setFadeInVisible(false);
            requestAnimationFrame(() => setFadeInVisible(true));
            setTimeout(() => {
              setCurrentSrc(newUrl);
              setFadeInSrc(null);
              setFadeInVisible(false);
            }, FADE_DURATION);
          };
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    };

    const interval = setInterval(checkForChange, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, []);

  useOverlayWebSocket(screenId, setOverlays);

  if (!currentSrc) {
    return <div style={{ background: "black", height: "100vh", color: "white" }}>Loading…</div>;
  }

  return (
    <div
      style={{
        background: "black",
        width: "100vw",
        height: "100vh",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <img
        src={currentSrc}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          objectFit: "contain",
          zIndex: 0,
        }}
        alt="Current image"
      />

      {fadeInSrc && (
        <img
          src={fadeInSrc}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            objectFit: "contain",
            zIndex: 1,
            opacity: fadeInVisible ? 1 : 0,
            transition: `opacity ${FADE_DURATION}ms ease`,
          }}
          alt="Next image"
        />
      )}

      {overlays.map((o) => (
        <div
          key={o.id}
          style={getOverlayStyle(o.position, o.visible)}
          dangerouslySetInnerHTML={{ __html: o.html }}
        />
      ))}
    </div>
  );
}
