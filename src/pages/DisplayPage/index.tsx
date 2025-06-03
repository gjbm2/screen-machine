import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import useBaseFileName from "./hooks/useBaseFileName";
import useFilePolling from "./hooks/useFilePolling";
import { MediaDisplay } from "./MediaDisplay";
import { OverlayContainer } from "./OverlayContainer";
import useOverlayWebSocket from "./hooks/useOverlayWebSocket";
import { useMask } from "./hooks/useMask";
import { Api } from "@/utils/api";
import { extractEventTriggers } from "@/utils/scheduleUtils";
import { Zap } from "lucide-react";

export default function DisplayPage() {
  const { screenId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const baseFileName = useBaseFileName(screenId);
  const filePolling = useFilePolling(baseFileName);
  const { currentSrc, videoKey: pollingVideoKey, lastModified, fileType, fileName } = filePolling;

  const [videoKey, setVideoKey] = useState(pollingVideoKey);
  const [overlays, setOverlays] = useState([]);

  // Scheduler status state
  const [schedulerStatus, setSchedulerStatus] = useState<any>(null);
  // Event triggers state
  const [eventTriggers, setEventTriggers] = useState<string[]>([]);
  // Polling intervals
  const statusIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const triggersIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Next event state
  const [nextEvent, setNextEvent] = useState<any>(null);
  const nextEventIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useOverlayWebSocket(screenId, setOverlays);

  // Check for nomask parameter
  const noMask = searchParams.has('nomask');
  const noColour = searchParams.has('nocolour');
  // Add display param for fit/fill
  const displayMode = searchParams.get('display') === 'fill' ? 'fill' : 'fit';
  const { brightness, warmHex, warmAlpha, rgba: brightnessLayer } = useMask(screenId, noMask);

  const containerRef = useRef(null);
  const [visibleSrc, setVisibleSrc] = useState<string | null>(null);
  const [fadingOut, setFadingOut] = useState(false);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [shouldPlay, setShouldPlay] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const controlsTimeout = useRef<NodeJS.Timeout | null>(null);

  // Poll scheduler status only when overlay is shown
  useEffect(() => {
    if (!screenId || !showControls) return;
    const api = new Api();
    let isMounted = true;
    const fetchStatus = async () => {
      try {
        const status = await api.getSchedulerStatus(screenId);
        if (isMounted) setSchedulerStatus(status);
      } catch (err) {
        if (isMounted) setSchedulerStatus(null);
      }
    };
    fetchStatus();
    statusIntervalRef.current = setInterval(fetchStatus, 15000);
    return () => {
      isMounted = false;
      if (statusIntervalRef.current) clearInterval(statusIntervalRef.current);
    };
  }, [screenId, showControls]);

  // Poll schedule/event triggers only when overlay is shown
  useEffect(() => {
    if (!screenId || !showControls) return;
    const api = new Api();
    let isMounted = true;
    const fetchSchedule = async () => {
      try {
        const res = await api.getSchedule(screenId);
        const schedule = res && (res.schedule || res);
        const triggers = extractEventTriggers(schedule);
        if (isMounted) setEventTriggers(triggers);
      } catch (err) {
        if (isMounted) setEventTriggers([]);
      }
    };
    fetchSchedule();
    triggersIntervalRef.current = setInterval(fetchSchedule, 30000);
    return () => {
      isMounted = false;
      if (triggersIntervalRef.current) clearInterval(triggersIntervalRef.current);
    };
  }, [screenId, showControls]);

  // Poll next scheduled event only when overlay is shown
  useEffect(() => {
    if (!screenId || !showControls) return;
    const api = new Api();
    let isMounted = true;
    const fetchNextEvent = async () => {
      try {
        const res = await api.getNextScheduledAction(screenId);
        if (isMounted) setNextEvent(res && res.next_action ? res.next_action : null);
      } catch (err) {
        if (isMounted) setNextEvent(null);
      }
    };
    fetchNextEvent();
    nextEventIntervalRef.current = setInterval(fetchNextEvent, 15000);
    return () => {
      isMounted = false;
      if (nextEventIntervalRef.current) clearInterval(nextEventIntervalRef.current);
    };
  }, [screenId, showControls]);

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

  // Show controls on hover (desktop) or tap (mobile)
  const handleShowControls = () => {
    setShowControls(true);
    if (controlsTimeout.current) clearTimeout(controlsTimeout.current);
    controlsTimeout.current = setTimeout(() => setShowControls(false), 4000);
  };

  // Toggle fit/fill
  const toggleDisplayMode = () => {
    const params = new URLSearchParams(searchParams);
    params.set('display', displayMode === 'fit' ? 'fill' : 'fit');
    navigate({ search: params.toString() }, { replace: true });
  };

  // Toggle nomask
  const toggleNoMask = () => {
    const params = new URLSearchParams(searchParams);
    if (noMask) {
      params.delete('nomask');
    } else {
      params.set('nomask', '1');
    }
    navigate({ search: params.toString() }, { replace: true });
  };

  // Handle throw event
  const handleThrowEvent = async (eventKey: string) => {
    const api = new Api();
    try {
      await api.throwEvent({ event: eventKey, scope: screenId, ttl: "60s" });
      // Optionally show a toast/alert
      // alert(`Event '${eventKey}' triggered`);
    } catch (err) {
      // alert('Failed to trigger event');
    }
  };

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
    <div
      ref={containerRef}
      style={outerStyle}
      onMouseMove={handleShowControls}
      onClick={handleShowControls}
    >
      {/* Controls Overlay */}
      {showControls && (
        <div
          style={{
            position: 'absolute',
            top: 20,
            right: 20,
            zIndex: 10001,
            background: 'rgba(0,0,0,0.7)',
            color: 'white',
            borderRadius: 12,
            padding: '1em 1.5em',
            boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
            display: 'flex',
            flexDirection: 'column',
            gap: '1em',
            fontSize: 18,
            cursor: 'pointer',
            userSelect: 'none',
            minWidth: 260,
            maxWidth: '90vw',
          }}
        >
          {/* File info */}
          <div style={{fontSize: 15, opacity: 0.85, marginBottom: 8}}>
            <div><b>File:</b> {fileName || '—'}</div>
            <div><b>Type:</b> {fileType || '—'}</div>
            <div><b>Last update:</b> {lastModified ? new Date(lastModified).toLocaleString() : '—'}</div>
          </div>
          {/* Scheduler status */}
          <div style={{fontSize: 15, opacity: 0.85, marginBottom: 8}}>
            <div><b>Scheduler:</b> {schedulerStatus ? (schedulerStatus.is_running ? 'Running' : (schedulerStatus.is_paused ? 'Paused' : 'Stopped')) : '—'}</div>
            <div><b>Next event:</b> {nextEvent && nextEvent.next_time ? `${nextEvent.description || ''} (${nextEvent.next_time})` : '—'}</div>
          </div>
          {/* Event triggers */}
          {eventTriggers.length > 0 && (
            <div style={{marginBottom: 8}}>
              <div style={{fontWeight: 600, fontSize: 15, marginBottom: 4}}>Event Triggers:</div>
              <div style={{display: 'flex', flexWrap: 'wrap', gap: 8}}>
                {eventTriggers.map(trigger => (
                  <button
                    key={trigger}
                    onClick={e => { e.stopPropagation(); handleThrowEvent(trigger); }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 4,
                      background: 'rgba(255, 193, 7, 0.12)',
                      border: '1px solid #ffc10755',
                      color: '#ffc107',
                      borderRadius: 6,
                      padding: '2px 10px',
                      fontSize: 15,
                      cursor: 'pointer',
                      fontWeight: 500,
                    }}
                    title={`Trigger event: ${trigger}`}
                  >
                    <Zap size={16} style={{marginRight: 4}} /> {trigger}
                  </button>
                ))}
              </div>
            </div>
          )}
          {/* Toggles */}
          <div onClick={e => { e.stopPropagation(); toggleDisplayMode(); }}
            style={{padding: '0.2em 0', cursor: 'pointer', textDecoration: 'underline', color: '#90caf9', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6}}>
            <span><b>Image:</b> {displayMode === 'fit' ? 'Fit' : 'Fill'}</span>
            <span style={{fontSize: 16, opacity: 0.7}}>&#8635;</span>
          </div>
          <div onClick={e => { e.stopPropagation(); toggleNoMask(); }}
            style={{padding: '0.2em 0', cursor: 'pointer', textDecoration: 'underline', color: '#90caf9', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6}}>
            <span><b>Mask:</b> {noMask ? 'Off' : 'On'}</span>
            <span style={{fontSize: 16, opacity: 0.7}}>&#8635;</span>
          </div>
        </div>
      )}
      {/* Main Media */}
      <MediaDisplay
        src={visibleSrc || currentSrc}
        fadeInSrc={null}
        fadeInVisible={false}
        videoKey={videoKey}
        fadeOut={fadingOut}
        shouldPlay={shouldPlay}
        displayMode={displayMode}
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
