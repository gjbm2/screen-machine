import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import useBaseFileName from "./hooks/useBaseFileName";
import useFilePolling from "./hooks/useFilePolling";
import { MediaDisplay } from "./MediaDisplay";
import { OverlayContainer } from "./OverlayContainer";
import useOverlayWebSocket from "./hooks/useOverlayWebSocket";
import { useMask } from "./hooks/useMask";

export default function DisplayPage() {
  const { screenId } = useParams();
  const [searchParams] = useSearchParams();
  const baseFileName = useBaseFileName(screenId);
  const filePolling = useFilePolling(baseFileName);
  const { currentSrc, videoKey: pollingVideoKey } = filePolling;

  const [videoKey, setVideoKey] = useState(pollingVideoKey);
  const [overlays, setOverlays] = useState([]);

  useOverlayWebSocket(screenId, setOverlays);

  // Check for nomask parameter
  const noMask = searchParams.has('nomask');
  const noColour = searchParams.has('nocolour');
  const { brightness, warmHex, warmAlpha, rgba: brightnessLayer } = useMask(screenId, noMask);

  const containerRef = useRef(null);
  const [visibleSrc, setVisibleSrc] = useState<string | null>(null);
  const [fadingOut, setFadingOut] = useState(false);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [shouldPlay, setShouldPlay] = useState(false);

  useEffect(() => {
    const existing = document.querySelector("meta[name=viewport]");
    const metaContent = "width=1920, initial-scale=0.5, maximum-scale=1.0, user-scalable=no";

    if (!existing) {
      const meta = document.createElement("meta");
      meta.name = "viewport";
      meta.content = metaContent;
      document.head.appendChild(meta);
    } else {
      existing.setAttribute("content", metaContent);
    }
  }, []);

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
  }, [currentSrc, visibleSrc, fadingOut]);

  if (!currentSrc) {
    return <div style={{ background: "black", height: "100vh", color: "white" }}>Loading…</div>;
  }
  
  const isFireTV = /AFT|FireTV|Amazon/i.test(navigator.userAgent);

  const outerStyle = {
    width: "100vw",
    height: "100vh", 
    background: "black",
    position: "fixed" as const,
    overflow: "hidden",
    top: 0,
    left: 0,
  };

  return (
    <div ref={containerRef} style={outerStyle}> 
      <MediaDisplay
        src={visibleSrc || currentSrc}
        fadeInSrc={null}
        fadeInVisible={false}
        videoKey={videoKey}
        fadeOut={fadingOut}
        shouldPlay={shouldPlay}
        onFadeOutComplete={() => {
          if (pendingSrc) {
            setVisibleSrc(pendingSrc);
            setPendingSrc(null);
          }

          setShouldPlay(false);

          setTimeout(() => {
            setVideoKey(pollingVideoKey);
            setFadingOut(false);
            setShouldPlay(true);
          }, 1000);
        }}
      />
      
      {/* Brightness layer */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundColor: brightnessLayer,
          pointerEvents: "none",
          transition: "background-color 1000ms ease-in-out",
          zIndex: 5,
        }}
      />

      {/* Warm color layer - only hide if nocolour is set */}
      {!noColour && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundColor: warmHex,
            mixBlendMode: "multiply",
            opacity: warmAlpha,
            pointerEvents: "none",
            transition: "background-color 1000ms ease-in-out, opacity 1000ms ease-in-out",
            zIndex: 6,
          }}
        />
      )}
      
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
  }, 1200);
}
