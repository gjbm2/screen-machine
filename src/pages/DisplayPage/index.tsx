import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import useBaseFileName from "./hooks/useBaseFileName";
import useFilePolling from "./hooks/useFilePolling";
import { MediaDisplay } from "./MediaDisplay";
import { OverlayContainer } from "./OverlayContainer";
import { JobIndicator } from "./JobIndicator";
import useOverlayWebSocket from "./hooks/useOverlayWebSocket";
import { useMask } from "./hooks/useMask";
import { useBlackBody } from "./hooks/useBlackBody";
import { Api } from "@/utils/api";
import { extractEventTriggers } from "@/utils/scheduleUtils";
import { useIsMobile } from "@/hooks/useIsMobile";
import { LayerBoundary } from "./common/LayerBoundary";
import { ControlsPanel, type NextAction, type SchedulerStatus } from "./ControlsPanel";
import { LegacyMaskLayers } from "./LegacyMaskLayers";
import { ArtOnlyPage } from "./art/ArtOnlyPage";
import { ArtMedia, type ArtMediaHandle } from "./art/ArtMedia";
import { MaskLayers } from "./art/MaskLayers";
import { isClip, mediaContainerStyle, mediaStyle } from "./art/styles";
import { LiveLayer } from "./live/LiveLayer";
import {
  DisplayController,
  controlSocketUrl,
  getDisplayController,
  releaseDisplayController,
  retainDisplayController,
  type ArtAdapter,
} from "./control/DisplayController";
import { initialCtlState, type CtlState } from "./control/store";
import type { ArtFlags, Role } from "./control/protocol";
import type { Overlay } from "./OverlayContainer";
import { roomSocketBase } from "./secureUrls";

declare global {
  interface Window {
    /** The controlled page's DisplayController (diagnostics and tests; only with ?ctl=). */
    __smCtl?: DisplayController;
  }
}


// Modes (LIVE_V1 §2):
//   /display/<id>                 today's page, unchanged (no control socket)
//   /display/<id>?ctl=ws://h:3001 the controlled page: control socket, mask push, freeze, live layer
//   /display/<id>?artonly=1       the snapshotter's art-only page (art/ArtOnlyPage.tsx)
export default function DisplayPageRoute() {
  useBlackBody();
  const [searchParams] = useSearchParams();
  const artOnly = searchParams.has("artonly") && searchParams.get("artonly") !== "0";
  return (
    <LayerBoundary name="page" fallback={<div style={{ position: "fixed", inset: 0, backgroundColor: "black" }} />}>
      {artOnly ? <ArtOnlyPage /> : <DisplayPage />}
    </LayerBoundary>
  );
}

const noopSubscribe = () => () => undefined;
const initialSnapshot = () => initialCtlState;

/** Role for the control socket: `?kiosk=north|south` -> kiosk, `?kiosk=lab` -> lab, otherwise viewer. */
// The room's control plane (VITE_ROOM_URL in .env). Every display page connects to it, so the TV URLs never change:
// art as before until room_service sends a live cutover; light masks are pushed, not polled.
// ?ctl=<ws url> points elsewhere and ?ctl=off disables it.
const DEFAULT_CTL = roomSocketBase(((import.meta.env.VITE_ROOM_URL as string | undefined) ?? "").replace(/^http/i, "ws"));

function roleFromParams(p: URLSearchParams): Role {
  const k = p.get("kiosk");
  if (!k) return "kiosk"; // a plain /display/<id> is a TV; ?kiosk=viewer watches without driving
  return k === "lab" ? "lab" : k === "viewer" ? "viewer" : "kiosk";
}

// [data-hidden] on the art stack is the cut: one attribute flip hides it (and anything a descendant
// might set visible). html[data-sm-live] is set while a live session is mounted (from live.attach to
// teardown, before any cut) so nothing else in the app (e.g. a toast) can draw over the video.
const ART_GUARD_CSS = [
  "[data-sm-art][data-hidden],[data-sm-art][data-hidden] *{visibility:hidden !important}",
  "html[data-sm-live] body>:not(#root),html[data-sm-live] #root>:not([data-sm-live-layer]):not([data-sm-art]){visibility:hidden !important}",
].join("\n");

function DisplayPage() {
  const { screenId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const baseFileName = useBaseFileName(screenId);
  const filePolling = useFilePolling(baseFileName);
  const { currentSrc, videoKey: pollingVideoKey, lastModified, fileType, fileName } = filePolling;

  const [videoKey, setVideoKey] = useState(pollingVideoKey);
  const [overlays, setOverlays] = useState<Overlay[]>([]);

  // Scheduler status state
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null);
  // Event triggers state
  const [eventTriggers, setEventTriggers] = useState<string[]>([]);
  // Polling intervals
  const statusIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const triggersIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Next event state
  const [nextEvent, setNextEvent] = useState<NextAction | null>(null);
  const nextEventIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Check for nomask parameter
  const noMask = searchParams.has('nomask');
  const noColour = searchParams.has('nocolour');
  // Add display param for fit/fill
  const displayMode = searchParams.get('display') === 'fill' ? 'fill' : 'fit';

  // --- Control plane: only with an explicit ?ctl=ws://host:port (LIVE_V1 §2). Otherwise ctl is null
  // --- and everything below behaves exactly as before.
  const role = roleFromParams(searchParams);
  const ctlUrl = screenId ? controlSocketUrl(searchParams.get("ctl") ?? DEFAULT_CTL, screenId, role) : null;
  const flags: ArtFlags = { nomask: noMask, nocolour: noColour, fit: displayMode };
  const flagsRef = useRef(flags);
  flagsRef.current = flags;
  const ctlKey = searchParams.get("ctlkey");
  // One controller per document load and configuration (the registry, not this memo, owns it).
  const ctl = useMemo(
    () =>
      ctlUrl && screenId
        ? getDisplayController({ ctlUrl, screenId, role, flags: flagsRef.current, ctlKey, reload: fadeToBlackAndReload })
        : null,
    [ctlUrl, screenId, role, ctlKey],
  );
  const ctlState: CtlState = useSyncExternalStore(ctl ? ctl.store.subscribe : noopSubscribe, ctl ? ctl.store.getState : initialSnapshot);
  const frozen = !!ctlState.frozen;
  const liveOnGlass = ctlState.layer === "live";
  // Overlays and the controls panel are dismissed while frozen and unmounted while live.
  const quiet = !!ctl && (frozen || liveOnGlass);
  const quietRef = useRef(quiet);
  quietRef.current = quiet;

  // While quiet, overlays never become visible (their own timers may still try).
  const setOverlaysGated = useCallback((u: React.SetStateAction<Overlay[]>) => {
    setOverlays((prev) => {
      const next = typeof u === "function" ? u(prev) : u;
      return quietRef.current ? next.map((o) => (o.visible ? { ...o, visible: false } : o)) : next;
    });
  }, []);
  const acceptOverlay = useCallback(() => {
    if (!quietRef.current) return true;
    ctl?.noteOverlayDropped();
    return false;
  }, [ctl]);

  useOverlayWebSocket(screenId, ctl ? setOverlaysGated : setOverlays, ctl ? acceptOverlay : undefined);

  const { brightness, warmHex, warmAlpha, rgba: brightnessLayer } = useMask(ctl ? undefined : screenId, noMask);

  const containerRef = useRef(null);
  const [visibleSrc, setVisibleSrc] = useState<string | null>(null);
  const [fadingOut, setFadingOut] = useState(false);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [shouldPlay, setShouldPlay] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const controlsTimeout = useRef<NodeJS.Timeout | null>(null);

  const isMobile = useIsMobile();

  // Global touch handler to show controls (ensures we catch touches even if elements overlay the container)
  useEffect(() => {
    const onTouch = () => handleShowControls();
    window.addEventListener('touchstart', onTouch, { passive: true });
    return () => window.removeEventListener('touchstart', onTouch);
  }, []);

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
    const metaContent = isMobile
      ? "width=device-width, initial-scale=1.0, user-scalable=yes"
      : "width=1920, initial-scale=0.5, maximum-scale=1.0, user-scalable=no";

    if (!existing) {
      const meta = document.createElement("meta");
      meta.name = "viewport";
      meta.content = metaContent;
      document.head.appendChild(meta);
    } else {
      existing.setAttribute("content", metaContent);
    }
  }, [isMobile]);

  // Server overlays' inline scripts don't run from innerHTML, so they are re-run here. Still needed by
  // overlay_spinner (the countdown ring in the prompt/schedule/explain/guidance panels); job progress
  // no longer comes as overlays (JobIndicator).
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

  // Timed hard reloads: today's schedule. The controlled page's are run by its DisplayController
  // instead (policy.timed_reload, and never while live/frozen/attaching: LIVE_V1 §2.2 rule 9).
  const ctlRef = useRef(ctl);
  ctlRef.current = ctl;
  useEffect(() => {
    if (ctlRef.current) return;
    scheduleNextHardReload();
  }, []);

  // Handle new media arrival
  useEffect(() => {
    if (ctl) return; // the controlled page's version is below
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
  }, [currentSrc, visibleSrc, fadingOut, ctl]);

  // --- Controlled page: base changes, freeze, cuts -------------------------------------------------
  const artMediaRef = useRef<ArtMediaHandle>(null);
  const shownSrc = visibleSrc || currentSrc;
  const shownSrcRef = useRef(shownSrc);
  shownSrcRef.current = shownSrc;
  /** performance.now() until which a through-black base change is still on screen. */
  const baseBusyUntil = useRef(0);
  /** performance.now() until which dismissed overlays are still fading out (freeze / live). */
  const overlaysBusyUntil = useRef(0);
  const frozenRef = useRef(frozen);
  frozenRef.current = frozen;
  const hiddenSwap = useRef<string | null>(null);
  const artStackRef = useCallback(
    (el: HTMLDivElement | null) => {
      containerRef.current = el;
      ctl?.attachArtStack(el);
    },
    [ctl],
  );

  // New media, controlled page: frozen -> queued (applied after unfreeze); art hidden under live ->
  // decoded first, then swapped with no transition; otherwise today's through-black change.
  useEffect(() => {
    if (!ctl || !currentSrc) return;
    if (!visibleSrc) {
      setVisibleSrc(currentSrc);
      setShouldPlay(true);
      return;
    }
    if (frozen) return;
    if (liveOnGlass) {
      if (currentSrc === visibleSrc || fadingOut || hiddenSwap.current === currentSrc) return;
      const target = currentSrc;
      const key = pollingVideoKey;
      hiddenSwap.current = target;
      void preloadBase(target).then(() => {
        hiddenSwap.current = null;
        if (frozenRef.current) return; // a freeze arrived meanwhile: this effect re-runs on unfreeze
        setVisibleSrc(target);
        setPendingSrc(null);
        setVideoKey(key);
        setShouldPlay(true);
      });
      return;
    }
    if (currentSrc !== pendingSrc && currentSrc !== visibleSrc && !fadingOut) {
      baseBusyUntil.current = Number.POSITIVE_INFINITY;
      setPendingSrc(currentSrc);
      setFadingOut(true);
    }
  }, [ctl, currentSrc, visibleSrc, fadingOut, frozen, liveOnGlass, pendingSrc, pollingVideoKey]);

  // Wire the controller to the art stack.
  useEffect(() => {
    if (!ctl) return;
    const adapter: ArtAdapter = {
      settle: async () => {
        await new Promise((r) => requestAnimationFrame(r)); // let React commit the freeze first
        const t0 = performance.now();
        const busy = () => performance.now() < Math.max(baseBusyUntil.current, overlaysBusyUntil.current);
        while (busy() && performance.now() - t0 < 4000) {
          await new Promise((r) => setTimeout(r, 50));
        }
        await artMediaRef.current?.ready();
      },
      snapshot: () => {
        const src = shownSrcRef.current ?? null;
        const media = artMediaRef.current;
        return {
          base_url: src,
          kind: media ? media.kind() : src && isClip(src) ? "video" : "image",
          clip_t: media ? media.clipTime() : null,
        };
      },
      prepareReveal: () => artMediaRef.current?.ready() ?? Promise.resolve(),
    };
    ctl.setArtAdapter(adapter);
    return () => ctl.setArtAdapter(null);
  }, [ctl]);

  useEffect(() => {
    ctl?.setFlags({ nomask: noMask, nocolour: noColour, fit: displayMode });
  }, [ctl, noMask, noColour, displayMode]);

  // Start/stop the control plane; expose it for diagnostics and tests.
  useEffect(() => {
    if (!ctl) return;
    retainDisplayController(ctl);
    window.__smCtl = ctl;
    return () => {
      releaseDisplayController(ctl);
      if (window.__smCtl === ctl) delete window.__smCtl;
    };
  }, [ctl]);

  useLayoutEffect(() => {
    if (!ctl) return;
    const style = document.createElement("style");
    style.setAttribute("data-sm-art-guard", "");
    style.textContent = ART_GUARD_CSS;
    document.head.appendChild(style);
    return () => style.remove();
  }, [ctl]);

  // Freeze / live: close the controls panel and dismiss overlays. They fade out over
  // FREEZE_OVERLAY_FADE_MS (not their normal 5 s) and the frozen ack waits for that, so the ArtState
  // (which has no overlays) is what is on glass, within room_service's 3 s ack budget.
  useLayoutEffect(() => {
    if (!quiet) return;
    setShowControls(false);
    setOverlays((prev) => {
      // Any overlay not yet on the short fade (visible, or still in its normal 5 s fade-out) gets it.
      const fading = prev.filter((o) => o.visible || o.fadeout === undefined);
      if (!fading.length) return prev;
      overlaysBusyUntil.current = performance.now() + FREEZE_OVERLAY_FADE_MS + 50;
      return prev.map((o) => (fading.includes(o) ? { ...o, visible: false, fadeout: FREEZE_OVERLAY_FADE_MS } : o));
    });
  }, [quiet]);

  // Show controls on hover (desktop) or tap (mobile)
  const handleShowControls = (e?: React.SyntheticEvent | Event) => {
    if (quietRef.current) return;
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

  const onFadeOutComplete = () => {
    if (pendingSrc) {
      setVisibleSrc(pendingSrc);
      setPendingSrc(null);
    }

    setShouldPlay(false);
    // Controlled page: the swap is on screen for 1 s of black, then a 1 s fade in.
    if (ctl) baseBusyUntil.current = performance.now() + 2100;

    setTimeout(() => {
      setVideoKey(pollingVideoKey);
      setFadingOut(false);
      setShouldPlay(true);
    }, 1000);
  };

  if (!currentSrc) {
    return <div style={{ background: "black", height: "100vh", color: "white" }}>Loading…</div>;
  }

  const isFireTV = /AFT|FireTV|Amazon/i.test(navigator.userAgent);

  const lastGoodSrc = visibleSrc || currentSrc;
  const mediaFallback =
    lastGoodSrc && !isClip(lastGoodSrc) ? (
      <div style={mediaContainerStyle}>
        <img src={lastGoodSrc} style={mediaStyle(displayMode)} alt="media" />
      </div>
    ) : null;

  const containerStyle: React.CSSProperties = {
    position: "fixed",
    inset: 0,
    width: "100vw",
    height: "100vh",
    backgroundColor: "black",
  };

  const artStack = (
    <div
      ref={ctl ? artStackRef : containerRef}
      data-sm-art={ctl ? "" : undefined}
      style={ctl ? { ...containerStyle, zIndex: 2 } : containerStyle}
      onPointerDown={(e) => {
        if (e.pointerType === 'touch') handleShowControls();
      }}
      onMouseMove={!isMobile ? handleShowControls : undefined}
      onTouchStart={handleShowControls}
    >
      {/* Controls Overlay */}
      {showControls && !quiet && (
        <LayerBoundary name="controls">
          <ControlsPanel
            isMobile={isMobile}
            fileName={fileName}
            fileType={fileType}
            lastModified={lastModified}
            schedulerStatus={schedulerStatus}
            nextEvent={nextEvent}
            eventTriggers={eventTriggers}
            displayMode={displayMode}
            noMask={noMask}
            toggleDisplayMode={toggleDisplayMode}
            toggleNoMask={toggleNoMask}
            handleThrowEvent={handleThrowEvent}
          />
        </LayerBoundary>
      )}
      {/* Main Media */}
      <LayerBoundary name="media" resetKey={lastGoodSrc} fallback={mediaFallback}>
        {ctl ? (
          <ArtMedia
            ref={artMediaRef}
            src={visibleSrc || currentSrc}
            videoKey={videoKey}
            displayMode={displayMode}
            mode="cycle"
            fadeOut={fadingOut}
            shouldPlay={shouldPlay}
            hold={frozen || liveOnGlass}
            onFadeOutComplete={onFadeOutComplete}
          />
        ) : (
          <MediaDisplay
            src={visibleSrc || currentSrc}
            fadeInSrc={null}
            fadeInVisible={false}
            videoKey={videoKey}
            fadeOut={fadingOut}
            shouldPlay={shouldPlay}
            displayMode={displayMode}
            onFadeOutComplete={onFadeOutComplete}
          />
        )}
      </LayerBoundary>

      <LayerBoundary name="mask">
        {ctl ? (
          <MaskLayers controller={ctl.mask} noColour={noColour} />
        ) : (
          <LegacyMaskLayers brightnessLayer={brightnessLayer} warmHex={warmHex} warmAlpha={warmAlpha} noColour={noColour} />
        )}
      </LayerBoundary>

      {!(ctl && liveOnGlass) && (
        <LayerBoundary name="overlays">
          <OverlayContainer overlays={overlays} />
        </LayerBoundary>
      )}

      {/* Generation jobs for this screen: always subscribed, hidden while quiet (not part of settle). */}
      {screenId && (
        <LayerBoundary name="jobs">
          <JobIndicator screenId={screenId} hidden={quiet} />
        </LayerBoundary>
      )}
    </div>
  );

  // Default page: the art stack alone, exactly as before. Controlled page: the live layer under it.
  if (!ctl) return artStack;
  return (
    <>
      <LayerBoundary name="live" onError={() => ctl.onLiveLayerError()}>
        <LiveLayer containerRef={ctl.attachLiveLayer} />
      </LayerBoundary>
      {artStack}
    </>
  );
}

/** Fade-out of overlays dismissed by a freeze (their normal exit is 5 s; room_service allows 3 s for the ack). */
const FREEZE_OVERLAY_FADE_MS = 600;

/** Decode a still before it is swapped into the hidden art stack (a clip is just swapped). */
function preloadBase(src: string): Promise<void> {
  if (isClip(src)) return Promise.resolve();
  const img = new Image();
  img.src = src;
  return Promise.race([img.decode().catch(() => undefined), new Promise<void>((r) => setTimeout(r, 4000))]);
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
