
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import useBaseFileName from "./hooks/useBaseFileName";
import useFilePolling from "./hooks/useFilePolling";
import useOverlayWebSocket from "./hooks/useOverlayWebSocket";
import { MediaDisplay } from "./MediaDisplay";
import { OverlayContainer } from "./OverlayContainer";

export default function DisplayPage() {
  const { screenId } = useParams();

  const baseFileName = useBaseFileName(screenId);
  const filePolling = useFilePolling(baseFileName);
  const { currentSrc, videoKey } = filePolling;
  
  // Since fadeInSrc and fadeInVisible are missing from filePolling's return type,
  // we'll provide default values
  const fadeInSrc = (filePolling as any).fadeInSrc || null;
  const fadeInVisible = (filePolling as any).fadeInVisible || false;

  const [videoKeyState, setVideoKey] = useState(videoKey);

  const [overlays, setOverlays] = useState([]);
  useOverlayWebSocket(screenId, setOverlays);

  const containerRef = useRef(null);
  const [visibleSrc, setVisibleSrc] = useState<string | null>(null);
  const [fadingOut, setFadingOut] = useState(false);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [shouldPlay, setShouldPlay] = useState(false);

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

  useEffect(() => {
    scheduleNextHardReload();
  }, []);

  // Handle new media arrival
  useEffect(() => {
    if (!currentSrc) return;

    if (!visibleSrc) {
      setVisibleSrc(currentSrc);
      setShouldPlay(true);
      return;
    }

    if (currentSrc !== pendingSrc && currentSrc !== visibleSrc && !fadingOut) {
      setPendingSrc(currentSrc);
      setFadingOut(true);
    }
  }, [currentSrc, visibleSrc, fadingOut, pendingSrc]);

  if (!currentSrc) {
    return <div style={{ background: "black", height: "100vh", color: "white" }}>Loading…</div>;
  }
  
  const isFireTV = /AFT|FireTV|Amazon/i.test(navigator.userAgent);

  const outerStyle: React.CSSProperties = {
    width: "100vw",
    height: "100vh", 
    background: "black",
    position: "fixed",
    overflow: "hidden",
    top: 0,
    left: 0,
  };

  return (
    <div ref={containerRef} style={outerStyle}> 
      <MediaDisplay
        src={visibleSrc || currentSrc}
        fadeInSrc={fadeInSrc}
        fadeInVisible={fadeInVisible}
        videoKey={videoKeyState}
        fadeOut={fadingOut}
        shouldPlay={shouldPlay}
        onFadeOutComplete={() => {
          // Do the swap after fading out (ensure fadeOut completes before switching)
          if (pendingSrc) {
            setVisibleSrc(pendingSrc);
            setPendingSrc(null);
          }

          setShouldPlay(false);

          // After 1000ms (to match the CSS fade-out duration)
          setTimeout(() => {
            // Now update the videoKey and start playing the video
            setVideoKey(filePolling.videoKey); // ✅ update key properly
            setFadingOut(false);
            setShouldPlay(true); // Signal to start playing the new media
          }, 1000); // Match fade-out duration
        }}
      />
      <OverlayContainer overlays={overlays} />
    </div>
  );
}

// === Reload Logic ===

function scheduleNextHardReload() {
  const now = new Date();
  const reloadHours = [2, 6, 10, 14, 18, 22];

  const nextReload = new Date(now);
  for (const hour of reloadHours) {
    const scheduled = new Date(now);
    scheduled.setHours(hour, 0, 0, 0);
    if (scheduled > now) {
      nextReload.setTime(scheduled.getTime());
      break;
    }
  }

  if (nextReload <= now) {
    nextReload.setDate(now.getDate() + 1);
    nextReload.setHours(2, 0, 0, 0);
  }

  const delay = nextReload.getTime() - now.getTime();
  console.log(
    `[reload] Scheduled reload at ${nextReload.toLocaleTimeString()} (${Math.round(
      delay / 1000,
    )}s from now)`
  );

  setTimeout(() => {
    console.log(`[reload] Fading to black before reload...`);
    fadeToBlackAndReload();
  }, delay);
} 

function fadeToBlackAndReload() {
  const fade = document.createElement("div");
  fade.style.position = "fixed";
  fade.style.top = "0";
  fade.style.left = "0";
  fade.style.width = "100vw";
  fade.style.height = "100vh";
  fade.style.backgroundColor = "black";
  fade.style.opacity = "0";
  fade.style.transition = "opacity 1s ease";
  fade.style.zIndex = "999999";
  fade.style.pointerEvents = "none";
  document.body.appendChild(fade);

  requestAnimationFrame(() => {
    fade.style.opacity = "1";
  });

  setTimeout(() => {
    location.reload();
  }, 1200); // 1s fade + 200ms buffer
}
