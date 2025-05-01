import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { getPublishDestinations } from "@/services/PublishService";

const POLL_INTERVAL = 2000;
const FADE_DURATION = 3500;
const OVERLAY_FADEIN_DURATION = 2000;
const OVERLAY_FADEOUT_DURATION = 5000;
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

function useOverlayWebSocket(
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

function getOverlayStyle(position: string | undefined, visible: boolean, fadein?: number): React.CSSProperties {
  const fadeDuration = visible
    ? (typeof fadein === "number" ? fadein : OVERLAY_FADEIN_DURATION)
    : OVERLAY_FADEOUT_DURATION;

  const baseStyle: React.CSSProperties = {
    position: "absolute",
    zIndex: 10000,
    opacity: visible ? 1 : 0,
    transition: fadein === 0 ? "none" : `opacity ${fadeDuration}ms ease`,
    pointerEvents: "none"
  };

  if (!position) {
    return { ...baseStyle, top: 0, left: 0, width: "100vw", height: "100vh" };
  }

  switch (position) {
    case "top-left": return { ...baseStyle, top: "20px", left: "20px" };
    case "top-center": return { ...baseStyle, top: "20px", left: "50%", transform: "translateX(-50%)" };
    case "top-right": return { ...baseStyle, top: "20px", right: "20px" };
    case "bottom-left": return { ...baseStyle, bottom: "20px", left: "20px" };
    case "bottom-center": return { ...baseStyle, bottom: "20px", left: "50%", transform: "translateX(-50%)" };
    case "bottom-right": return { ...baseStyle, bottom: "20px", right: "20px" };
    default: return { ...baseStyle, bottom: "20px", left: "50%", transform: "translateX(-50%)" };
  }
}

export default function DisplayPage() {
  const { screenId } = useParams();
  const [currentSrc, setCurrentSrc] = useState<string | null>(null);
  const [fadeInSrc, setFadeInSrc] = useState<string | null>(null);
  const [fadeInVisible, setFadeInVisible] = useState(false);
  const [videoKey, setVideoKey] = useState<string>("initial");
  const [overlays, setOverlays] = useState<Overlay[]>([]);

  const lastModifiedRef = useRef<number>(0);
  const baseFileNameRef = useRef<string | null>(null);

  useEffect(() => {
    const destinations = getPublishDestinations();
    const matched = destinations.find(d => d.id === screenId && d.type === "output_file");
    if (matched) {
      baseFileNameRef.current = matched.file.replace(/\.(jpg|mp4)$/, "");
    }
  }, [screenId]);

  useEffect(() => {
    if (!baseFileNameRef.current) return;

    const detectInitialFile = async () => {
      const base = `/output/${baseFileNameRef.current}`;
      const jpgUrl = `${base}.jpg`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = jpgModified > mp4Modified ? "jpg" : "mp4";
      const latestModified = Math.max(jpgModified, mp4Modified);
      lastModifiedRef.current = latestModified;

      const initialUrl = latestType === "jpg"
        ? `${base}.jpg?t=${latestModified}`
        : `${base}.mp4?t=${latestModified}`;

      setCurrentSrc(initialUrl);
      setVideoKey(`${Date.now()}`);
    };

    detectInitialFile();

    const checkForChange = async () => {
      const base = `/output/${baseFileNameRef.current}`;
      const jpgUrl = `${base}.jpg`;
      const mp4Url = `${base}.mp4`;

      const [jpgRes, mp4Res] = await Promise.allSettled([
        fetch(jpgUrl, { method: "HEAD" }),
        fetch(mp4Url, { method: "HEAD" }),
      ]);

      const jpgModified = jpgRes.status === "fulfilled"
        ? new Date(jpgRes.value.headers.get("last-modified") || 0).getTime()
        : 0;
      const mp4Modified = mp4Res.status === "fulfilled"
        ? new Date(mp4Res.value.headers.get("last-modified") || 0).getTime()
        : 0;

      const latestType = jpgModified > mp4Modified ? "jpg" : "mp4";
      const latestModified = Math.max(jpgModified, mp4Modified);

      if (latestModified > lastModifiedRef.current) {
        lastModifiedRef.current = latestModified;

        const newUrl = latestType === "jpg"
          ? `${base}.jpg?t=${latestModified}`
          : `${base}.mp4?t=${latestModified}`;

        setFadeInSrc(newUrl);
        setFadeInVisible(false);
        requestAnimationFrame(() => setFadeInVisible(true));
        setTimeout(() => {
          setCurrentSrc(newUrl);
          setVideoKey(`${Date.now()}`);
          setFadeInSrc(null);
          setFadeInVisible(false);
        }, FADE_DURATION);
      }
    };

    const interval = setInterval(checkForChange, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [screenId]);

  useOverlayWebSocket(screenId, setOverlays);

  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (containerRef.current) {
      const scripts = containerRef.current.querySelectorAll("script");
      scripts.forEach((script) => {
        const scriptElement = document.createElement("script");
        scriptElement.textContent = script.textContent;
        document.body.appendChild(scriptElement);
      });
    }
  }, [overlays]);

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
      ref={containerRef}
    >
      {currentSrc.endsWith(".mp4?t=") || currentSrc.includes(".mp4?") ? (
        <video
          key={videoKey}
          src={currentSrc}
          autoPlay
          muted
          loop
          playsInline
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            objectFit: "contain",
            zIndex: 0,
          }}
        />
      ) : (
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
      )}

      {fadeInSrc?.includes(".mp4") ? (
        <video
          src={fadeInSrc}
          autoPlay
          muted
          loop
          playsInline
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
        />
      ) : fadeInSrc ? (
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
      ) : null}

		{overlays.map((o) => (
		  <div
			key={o.id}
			style={{
			  ...getOverlayStyle(o.position, o.visible, o.fadein),
			  fontSize: "clamp(0.5rem, 2.5vw, 1.8rem)", // ← font scaling here
			}}
			dangerouslySetInnerHTML={{ __html: o.html }}
		  />
		))}
    </div>
  );
}
