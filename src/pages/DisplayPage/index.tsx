import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import useBaseFileName from "./hooks/useBaseFileName";
import useFilePolling from "./hooks/useFilePolling";
import useScreenState from "./hooks/useScreenState";
import { baseUrl, stillAspect, transitionToPlay, type ScreenState } from "./art/screenState";
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
import { ControlsPanel, type NextAction, type PanelClip, type SchedulerStatus } from "./ControlsPanel";
import { chooseClip, getPublishedInfo, playOnScreen, publishOwnClip, type PublishedClip, type PublishedTransition } from "@/shared/api/buckets";
import { LegacyMaskLayers } from "./LegacyMaskLayers";
import { ArtOnlyPage } from "./art/ArtOnlyPage";
import { ArtMedia, type ArtMediaHandle } from "./art/ArtMedia";
import { TransitionLayer, transitionFor } from "./art/TransitionLayer";
import { ClipLayer, type ClipLayerHandle } from "./art/ClipLayer";
import { BaseLanding } from "./art/baseLanding";
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
    /** Lifts the black the page loads under (index.html). */
    __smReveal?: () => void;
    /** Fades the page to black; the kiosk calls it before restarting its browser (kiosk_cycle.py). */
    __smFadeOut?: () => Promise<void>;
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

/** A phone or tablet viewing a display page (never a TV or the kiosk's browser): it keeps the device's own width.
 *  Told by the device, not innerWidth, which the TV's 1920-wide layout below sets itself: a phone used to get that
 *  layout, shrunk to a fifth or cropped to its top left, with fit/fill and the rest out of reach (owner, 24 Nov). */
const IS_HANDHELD = (() => {
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  if (/AFT|FireTV|Amazon|SmartTV|Tizen|Web0S|\bTV\b/i.test(ua)) return false;
  return /Mobi|iPhone|iPad|Android/i.test(ua) || (navigator.maxTouchPoints > 1 && /Macintosh/i.test(ua));
})();

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

  const baseFileName = useBaseFileName(screenId);
  // What the screen shows, as one document (art/screenState.ts): on the controlled page it replaces reading the
  // screen's files once the screen has one. The base is the still (or a published video); a still's clip is not.
  const { state: screenState, available: stateMode, checked: stateChecked } = useScreenState(ctl ? baseFileName : null, ctl);
  // the controlled page reads the screen's files only when the screen has no document (and only once that is known)
  const filePolling = useFilePolling(baseFileName, ctl ? stateChecked && !stateMode : true);
  const stateBase = stateMode ? baseUrl(screenState) : null;
  const currentSrc = stateBase ?? filePolling.currentSrc;
  const pollingVideoKey = stateBase ?? filePolling.videoKey;
  const fileType = stateMode ? (screenState?.video ? "mp4" : "jpg") : filePolling.fileType;
  const fileName = stateMode ? `${baseFileName}.${fileType}` : filePolling.fileName;
  const lastModified = stateMode ? Math.round((screenState?.at ?? 0) * 1000) : filePolling.lastModified;
  const [videoKey, setVideoKey] = useState(pollingVideoKey);

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

  // A published picture's caption waits until that picture has cross-faded in (art/baseLanding.ts).
  const baseLanding = useMemo(() => new BaseLanding(), []);
  useEffect(() => () => baseLanding.dispose(), [baseLanding]);
  const whenBaseLands = useCallback((show: () => void) => baseLanding.whenLanded(show), [baseLanding]);

  useOverlayWebSocket(
    screenId,
    ctl ? setOverlaysGated : setOverlays,
    ctl ? acceptOverlay : undefined,
    ctl ? whenBaseLands : undefined,
  );

  const { brightness, warmHex, warmAlpha, rgba: brightnessLayer } = useMask(ctl ? undefined : screenId, noMask);

  const containerRef = useRef(null);
  const [visibleSrc, setVisibleSrc] = useState<string | null>(null);
  const [fadingOut, setFadingOut] = useState(false);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [shouldPlay, setShouldPlay] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const controlsTimeout = useRef<NodeJS.Timeout | null>(null);

  const isMobile = useIsMobile() || IS_HANDHELD;

  // Touch: a plain one-finger tap opens the controls and another closes them; a pinch, a pan or a double tap (zoom)
  // leaves them alone, so a phone can look over and zoom into the whole picture (owner, 24 Nov: "all I see is the
  // mouseover view immediately and it prevents navigation if I WANT to zoom in"). On the window, so overlays on top
  // of the art don't swallow it; taps on the panel itself are its own.
  useEffect(() => {
    let start: { x: number; y: number; t: number } | null = null;
    let multi = false; // more than one finger, or one that moved: not a tap
    let pending: ReturnType<typeof setTimeout> | null = null;
    const onStart = (e: TouchEvent) => {
      if (e.touches.length > 1) multi = true;
      else if (!start) {
        multi = false;
        start = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() };
      }
    };
    const onMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (start && t && Math.hypot(t.clientX - start.x, t.clientY - start.y) >= 10) multi = true; // a pan, not a tap
    };
    const onEnd = (e: TouchEvent) => {
      if (e.touches.length > 0 || !start) return;
      const tap = !multi && Date.now() - start.t < 350;
      start = null;
      if (!tap || (e.target as Element | null)?.closest?.("[data-sm-controls]")) return;
      if (pending) {
        // a second tap: a double tap, the browser's zoom
        clearTimeout(pending);
        pending = null;
        return;
      }
      pending = setTimeout(() => {
        pending = null;
        if (controlsTimeout.current) clearTimeout(controlsTimeout.current);
        setShowControls((shown) => !shown && !quietRef.current);
      }, 300);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchmove", onMove, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
      if (pending) clearTimeout(pending);
    };
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

  // A frontend deploy reaches the TVs by itself (owner, 23 Nov): once a minute the page reads the served index, and
  // when it names another bundle the page fades to black and reloads (the controlled page waits while live, frozen or
  // just back from live, as for a timed reload). The kiosk's browser cycle (kiosk_cycle.py) fades through here too.
  useEffect(() => {
    window.__smFadeOut = fadeToBlack;
    const mine = ownBundle();
    if (!mine) return;
    const t = setInterval(() => {
      servedBundle().then(
        (served) => {
          if (!served || served === mine) return;
          clearInterval(t);
          console.log(`[reload] new build ${served} (this page runs ${mine})`);
          if (ctlRef.current) ctlRef.current.requestReload("new build");
          else fadeToBlackAndReload();
        },
        () => {}, // the server is restarting: next minute
      );
    }, BUILD_CHECK_MS);
    return () => clearInterval(t);
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
  // A still's own clip, played over it now and then (art/ClipLayer.tsx; state documents only)
  const clipLayerRef = useRef<ClipLayerHandle>(null);
  // A video to play once over what the screen shows (the controls panel's Play, on every page of the screen)
  useEffect(() => ctl?.onArtPlay((url) => void clipLayerRef.current?.playUrl(url)), [ctl]);
  // Each state document by its base, so a transition knows the picture it starts from (art/screenState.ts)
  const statesByBase = useRef(new Map<string, ScreenState>());
  useEffect(() => {
    const b = baseUrl(screenState);
    if (b && screenState) statesByBase.current.set(b, screenState);
    if (statesByBase.current.size > 20) statesByBase.current.delete(statesByBase.current.keys().next().value as string);
  }, [screenState]);
  const shownSrc = visibleSrc || currentSrc;
  const shownSrcRef = useRef(shownSrc);
  shownSrcRef.current = shownSrc;

  // The published picture's clips, for the controls panel's Play (read while it is open)
  const [published, setPublished] = useState<{ filename: string; clips: PublishedClip[]; transitions: PublishedTransition[] } | null>(null);
  const [canPlayNow, setCanPlayNow] = useState(false);
  const [easedOnScreen, setEasedOnScreen] = useState(false);
  const [startingClip, setStartingClip] = useState<string | null>(null);
  const playWait = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!screenId || !showControls) return;
    let live = true;
    const read = () =>
      getPublishedInfo(screenId).then((r) => live && setPublished({ filename: r.filename, clips: r.clips ?? [], transitions: r.transitions ?? [] }), () => live && setPublished(null));
    read();
    const t = setInterval(read, 15000);
    const poll = () => {
      const layer = clipLayerRef.current;
      setCanPlayNow(!!(layer?.url() ? layer.canPlayNow() : artMediaRef.current?.canPlayNow()));
      setEasedOnScreen(!!(layer?.url() || artMediaRef.current?.isEasedClip()));
    };
    const p = setInterval(poll, 500);
    poll();
    return () => {
      live = false;
      clearInterval(t);
      clearInterval(p);
    };
  }, [screenId, showControls]);
  useEffect(() => () => {
    if (playWait.current) clearInterval(playWait.current);
  }, []);
  const listed: PanelClip[] = (published?.clips ?? []).map((c) => ({ ...c, onScreen: c.filename === published?.filename && (isClip(shownSrc ?? "") || !!screenState?.clip) }));
  // The clip on screen plays here whatever the list says: the list (the picture's other clips) needs the screen's
  // access, which a page on plain http can't carry for an admins-only screen such as devtest (owner, 24 Nov)
  const clipsListed: PanelClip[] =
    easedOnScreen && !listed.some((c) => c.onScreen) ? [{ filename: shownSrc ?? "", onScreen: true }, ...listed] : listed;
  // and the transitions into and out of the picture, played once over it on the screen (owner, 25 Nov)
  const panelClips: PanelClip[] = [
    ...clipsListed,
    ...(published?.transitions ?? [])
      .slice()
      .sort((a, b) => (a.direction === b.direction ? 0 : a.direction === "in" ? -1 : 1))
      .map((t): PanelClip => ({ filename: t.url, url: t.url, kind: t.direction, idea: t.idea, play_s: t.play_s, onScreen: false })),
  ];

  /** Play the clip on screen now; another of the picture's clips is chosen, put up, and played as soon as it lands
   *  (rather than after its first short rest). */
  const playClip = async (clip: PanelClip) => {
    if (!screenId) return;
    if ((clip.kind === "in" || clip.kind === "out") && clip.url) {
      // to every page of the screen (the TV too), through room_service; it plays in each page's clip layer
      try {
        await playOnScreen(screenId, clip.url);
      } catch (e) {
        console.warn("[play] could not play the transition", e);
      }
      return;
    }
    if (clip.onScreen) {
      if (clipLayerRef.current?.url() ? clipLayerRef.current.playNow() : artMediaRef.current?.playNow()) setCanPlayNow(false);
      return;
    }
    const from = shownSrcRef.current;
    const fromClip = clipLayerRef.current?.url() ?? null;
    setStartingClip(clip.filename);
    try {
      await chooseClip(screenId, clip.filename);
      await publishOwnClip(screenId, clip.filename);
    } catch (e) {
      console.warn("[play] could not put the clip up", e);
      setStartingClip(null);
      return;
    }
    if (playWait.current) clearInterval(playWait.current);
    const until = Date.now() + 90_000;
    playWait.current = setInterval(() => {
      // a still's clip arrives as the still's new clip (state documents), else as the screen's new file
      const layer = clipLayerRef.current;
      const landed = layer?.url() && layer.url() !== fromClip ? layer.playNow() : shownSrcRef.current !== from && !!artMediaRef.current?.playNow();
      const done = Date.now() > until || landed;
      if (!done) return;
      if (playWait.current) clearInterval(playWait.current);
      playWait.current = null;
      setStartingClip(null);
    }, 500);
  };
  /** performance.now() until which a base change (decode + cross-fade) is still on screen. */
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
  // decoded first, then swapped with no transition; otherwise cross-faded in over the current base
  // (`incoming`, below). One change at a time: a newer file waits for the running one to land.
  const [incoming, setIncoming] = useState<{ src: string; videoKey: string } | null>(null);
  // A scheduled picture's transition (art/TransitionLayer.tsx): looked up for each new base, played over the old one,
  // and the cross-fade then happens under it (`covered`)
  const [transition, setTransition] = useState<{ for: string; url: string | null; aspect?: number | null; covered: boolean } | null>(null);
  const playedTransitions = useRef(new Set<string>());
  const transitionBusyRef = useRef(false);
  transitionBusyRef.current = !!(transition && (transition.url || !transition.covered));
  const [incomingShown, setIncomingShown] = useState(false);
  const incomingMediaRef = useRef<ArtMediaHandle>(null);
  useEffect(() => {
    if (!ctl || !currentSrc) return;
    if (!visibleSrc) {
      setVisibleSrc(currentSrc);
      setShouldPlay(true);
      return;
    }
    if (frozen || incoming) return;
    if (liveOnGlass) {
      if (currentSrc === visibleSrc || hiddenSwap.current === currentSrc) return;
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
    if (currentSrc === visibleSrc) return;
    if (transition?.for !== currentSrc) {
      const target = currentSrc;
      setTransition({ for: target, url: null, covered: false });
      const shownState = visibleSrc ? statesByBase.current.get(visibleSrc) ?? null : null;
      const fromState = stateMode && screenState && baseUrl(screenState) === target
        ? { t: transitionToPlay(shownState, screenState, Date.now() / 1000, playedTransitions.current) }
        : undefined;
      void transitionFor(target, playedTransitions.current, () => artMediaRef.current, fromState, () => !!clipLayerRef.current?.playing()).then((found) =>
        setTransition((t) => (t && t.for === target ? { ...t, url: found?.url ?? null, aspect: found?.aspect, covered: !found } : t)),
      );
      return;
    }
    if (!transition.covered) return;
    baseBusyUntil.current = Number.POSITIVE_INFINITY;
    baseLanding.started();
    setIncomingShown(false);
    setIncoming({ src: currentSrc, videoKey: pollingVideoKey });
    // the old picture's caption goes with it
    setOverlays((prev) =>
      prev.map((o) => (o.withBase && o.visible ? { ...o, visible: false, fadeout: CROSSFADE_MS } : o)),
    );
  }, [ctl, currentSrc, visibleSrc, frozen, liveOnGlass, incoming, pollingVideoKey, baseLanding, transition, stateMode, screenState]);
  // a freeze or the live layer ends a transition at once (the art stack is about to be held or hidden)
  useEffect(() => {
    if (quiet) setTransition((t) => (t && (t.url || !t.covered) ? { ...t, url: null, covered: true } : t));
  }, [quiet]);

  // The cross-fade: the new base is mounted invisible on top, decoded (a clip: paused on frame 0, so only one clip
  // decodes at a time), faded in on the compositor (opacity only: cheap on the kiosk's N95), and then the old one goes
  // and the new one starts its clip loop. A freeze's ack waits for it (baseBusyUntil).
  useEffect(() => {
    if (!incoming) return;
    let alive = true;
    let done: ReturnType<typeof setTimeout> | undefined;
    void (incomingMediaRef.current?.prime() ?? Promise.resolve()).then(() => {
      if (!alive) return;
      baseBusyUntil.current = performance.now() + CROSSFADE_MS + 150;
      setIncomingShown(true);
      done = setTimeout(() => {
        setVisibleSrc(incoming.src);
        setPendingSrc(null);
        setVideoKey(incoming.videoKey);
        setShouldPlay(true);
        setIncoming(null);
        setIncomingShown(false);
        baseLanding.landed();
      }, CROSSFADE_MS + 50);
    });
    return () => {
      alive = false;
      clearTimeout(done);
    };
  }, [incoming, baseLanding]);

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
        // a still's clip fades back to the still (ClipLayer, quiet): the frozen screen is the still
        await Promise.race([clipLayerRef.current?.settled() ?? Promise.resolve(), new Promise((r) => setTimeout(r, 1500))]);
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

  // The page loads under black (index.html #sm-cover): lift it (a 1 s fade) once the first picture is decoded and on
  // glass, or live is showing. Never held past 6 s here (and index.html lifts it at 12 s whatever happens).
  const revealed = useRef(false);
  useEffect(() => {
    if (revealed.current || !(visibleSrc || liveOnGlass)) return;
    let alive = true;
    const shown = liveOnGlass ? Promise.resolve() : (artMediaRef.current?.ready() ?? Promise.resolve());
    void Promise.race([shown.catch(() => undefined), new Promise((r) => setTimeout(r, 6000))]).then(() => {
      if (!alive || revealed.current) return;
      revealed.current = true;
      requestAnimationFrame(() => window.__smReveal?.());
    });
    return () => {
      alive = false;
    };
  }, [visibleSrc, liveOnGlass]);

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

  // Show controls on hover (a mouse; a tap on a touch screen is above)
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
      // a real mouse at any width (a phone's taps are above; its emulated mouse events come as touch pointers)
      onPointerMove={(e) => e.pointerType === "mouse" && handleShowControls()}
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
            clips={panelClips}
            canPlayNow={canPlayNow}
            startingClip={startingClip}
            onPlayClip={playClip}
          />
        </LayerBoundary>
      )}
      {/* Main Media */}
      <LayerBoundary name="media" resetKey={lastGoodSrc} fallback={mediaFallback}>
        {ctl ? (
          // Keyed by videoKey, so the incoming layer stays the same element when it becomes the current one.
          [{ src: visibleSrc || currentSrc, key: videoKey, current: true }, ...(incoming ? [{ src: incoming.src, key: incoming.videoKey, current: false }] : [])].map(
            (l) => (
              <div
                key={l.key}
                style={{
                  position: "absolute",
                  inset: 0,
                  zIndex: 0,
                  opacity: l.current || incomingShown ? 1 : 0,
                  transition: l.current ? "none" : `opacity ${CROSSFADE_MS}ms ${CROSSFADE_EASING}`,
                  willChange: "opacity", // keeps an invisible incoming layer rastered, so its fade starts smooth
                }}
              >
                <ArtMedia
                  ref={l.current ? artMediaRef : incomingMediaRef}
                  src={l.src}
                  videoKey={l.key}
                  displayMode={displayMode}
                  mode="cycle"
                  shouldPlay={l.current && shouldPlay}
                  hold={frozen || liveOnGlass}
                />
              </div>
            ),
          )
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

      {ctl && transition?.url && !quiet && (
        <LayerBoundary name="transition">
          <TransitionLayer
            src={transition.url}
            aspect={transition.aspect ?? null}
            displayMode={displayMode}
            onCovered={() => setTransition((t) => (t && !t.covered ? { ...t, covered: true } : t))}
            onDone={() => setTransition((t) => (t ? { ...t, url: null, covered: true } : t))}
          />
        </LayerBoundary>
      )}

      {ctl && stateMode && (
        <LayerBoundary name="clip">
          <ClipLayer
            ref={clipLayerRef}
            clip={screenState?.still && !screenState.video && visibleSrc === screenState.still.url ? screenState.clip : null}
            aspect={stillAspect(screenState)}
            displayMode={displayMode}
            quiet={quiet || !!incoming || !!(transition && (transition.url || !transition.covered))}
            slotFree={() => !transitionBusyRef.current}
          />
        </LayerBoundary>
      )}

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

/** The controlled page's dissolve from one base to the next (the default page still goes through black). */
const CROSSFADE_MS = 1600;
const CROSSFADE_EASING = "cubic-bezier(0.45, 0, 0.55, 1)";

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

/** Fade the whole page to black (1 s); resolves once it is black. The new page then starts under black and fades in
 *  (index.html #sm-cover), so a reload or a browser restart never goes through white. */
function fadeToBlack(): Promise<void> {
  const fade = document.createElement("div");
  fade.style.position = "fixed";
  fade.style.top = "0";
  fade.style.left = "0";
  fade.style.width = "100vw";
  fade.style.height = "100vh";
  fade.style.backgroundColor = "black";
  fade.style.opacity = "0";
  fade.style.transition = "opacity 1s ease";
  fade.style.zIndex = "2147483647";
  fade.style.pointerEvents = "none";
  document.body.appendChild(fade);

  requestAnimationFrame(() => {
    fade.style.opacity = "1";
  });
  return new Promise((r) => setTimeout(r, 1200));
}

function fadeToBlackAndReload() {
  void fadeToBlack().then(() => location.reload());
}

/** The bundle this page runs, and the one the server now serves (null: dev server, or not readable). */
function ownBundle(): string | null {
  return document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.getAttribute("src") ?? null;
}
async function servedBundle(): Promise<string | null> {
  const r = await fetch("/", { cache: "no-store" });
  if (!r.ok) return null;
  return (await r.text()).match(/<script[^>]*type="module"[^>]*src="([^"]*\/assets\/[^"]+)"/)?.[1] ?? null;
}

/** How often a TV page looks for a new build. */
const BUILD_CHECK_MS = 60_000;
