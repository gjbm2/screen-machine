// The still/clip layer of the art stack for the controlled (?ctl=) and art-only pages.
//
// Renders the same DOM and styles as MediaDisplay.tsx (which stays untouched for the default page),
// with two additions the live cut needs:
//   * `hold`: pause a clip on one frame (freeze, or while the art stack is hidden under live);
//   * an imperative handle that reports what is drawn (`kind`, `clip_t`) and resolves once the base
//     is decoded, so a freeze ack, an exit cut or an art-only render never races the decoder.
// mode="cycle": the TV (slow-motion clip loop). A new base is a second ArtMedia cross-faded in over this one by the
//   page (index.tsx): it is mounted with shouldPlay false, `prime()`d (decoded / frame 0 ready), faded in, and only
//   then plays, so at most one clip is decoding at a time (the N95 kiosk).
// mode="static": the art-only page (no fades, no loop; a clip is shown paused at `clipT`).
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Fit } from "../control/protocol";
import { ClipCycle, CROSSFADE_DURATION, EASED_FADE, FIRST_FRAME_FADE_OUT_DURATION, PACE, type EasedLoop } from "./ClipCycle";
import { isClip, mediaContainerStyle, mediaStyle } from "./styles";

const OPACITY_EASING = "cubic-bezier(0.65, 0, 0.35, 1)";
const READY_TIMEOUT_MS = 4000;
/** A new base that hasn't decoded by now fades in anyway (the freeze ack waits for the whole change). */
const PRIME_TIMEOUT_MS = 2000;

export interface ArtMediaHandle {
  kind(): "image" | "video";
  /** Position of a held/static clip, else null (a playing clip has no single frame). */
  clipTime(): number | null;
  /** Resolves once the base is decoded (img.decode) or a held/static clip shows its frame. */
  ready(): Promise<void>;
  /** A new base about to fade in: resolves once the still is decoded or the (paused) clip shows frame 0. */
  prime(): Promise<void>;
  element(): HTMLImageElement | HTMLVideoElement | null;
  /** A screen clip resting on its picture: it can play now (the controls panel's Play). */
  canPlayNow(): boolean;
  /** Play the resting clip now; false when it can't (see ClipCycle.playNow). */
  playNow(): boolean;
}

interface Props {
  src: string;
  videoKey: string;
  displayMode: Fit;
  mode: "cycle" | "static";
  /** Overrides the ".mp4" test (the art-only page is told the kind in its ArtState). */
  kind?: "image" | "video";
  shouldPlay?: boolean;
  hold?: boolean;
  clipT?: number | null;
}

function withTimeout(p: Promise<unknown>, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    p.then(
      () => {
        clearTimeout(t);
        resolve();
      },
      () => {
        clearTimeout(t);
        resolve();
      },
    );
  });
}

function waitEvent(el: HTMLMediaElement, ok: string, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(t);
      el.removeEventListener(ok, done);
      el.removeEventListener("error", done);
      resolve();
    };
    const t = setTimeout(done, ms);
    el.addEventListener(ok, done);
    el.addEventListener("error", done);
  });
}

/** A clip's play info is in its own MP4 comment tag, JSON {"clip": {"eased", "pace", "rest_s"}} (routes/clip_ease.py).
 *  The tag sits in the file's header, which is at the front (faststart): read the first 64 KB and find it there. */
async function clipInfo(src: string): Promise<Record<string, any> | null> {
  try {
    const r = await fetch(src, { headers: { Range: "bytes=0-65535" }, cache: "no-store" });
    if (!r.ok) return null;
    const head = new TextDecoder("latin1").decode((await r.arrayBuffer()).slice(0, 65536));
    const at = head.indexOf('{"clip"');
    if (at < 0) return null;
    let depth = 0;
    for (let i = at; i < head.length; i++) {
      if (head[i] === "{") depth++;
      else if (head[i] === "}" && --depth === 0) return JSON.parse(head.slice(at, i + 1)).clip ?? null;
    }
  } catch {
    // unreadable: play it the old way
  }
  return null;
}

/** A clip with its ease rendered in plays at 1x and rests; any other video plays the old eased loop. */
function easedLoop(clip: Record<string, any> | null): EasedLoop | null {
  if (clip?.eased && Array.isArray(clip.rest_s))
    return { restS: [Number(clip.rest_s[0]), Number(clip.rest_s[1])], returns: clip.returns !== false };
  return null;
}

function twoFrames(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
}

/** Seek a paused clip to `t` and wait for the frame (art-only renders). */
async function seekClip(v: HTMLVideoElement, t: number): Promise<void> {
  v.pause();
  if (v.readyState < 1) await waitEvent(v, "loadedmetadata", READY_TIMEOUT_MS);
  const target = Math.max(0, Number.isFinite(v.duration) ? Math.min(t, v.duration) : t);
  const seeked = waitEvent(v, "seeked", READY_TIMEOUT_MS);
  v.currentTime = target; // always seeks, even to the current position, so `seeked` fires
  await seeked;
  const t0 = performance.now();
  while (v.readyState < 2 && performance.now() - t0 < READY_TIMEOUT_MS) await twoFrames();
}

export const ArtMedia = forwardRef<ArtMediaHandle, Props>(function ArtMedia(
  { src, videoKey, displayMode, mode, kind, shouldPlay = false, hold = false, clipT = null },
  ref,
) {
  const isVideo = kind ? kind === "video" : isClip(src);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const cycleRef = useRef<ClipCycle | null>(null);
  const holdRef = useRef(hold);
  const holdDone = useRef<Promise<void>>(Promise.resolve());
  // The <video> is replaced per videoKey and dropped when a cross-fade ends: free its decoder there and then
  // rather than at GC (a ref callback, as StrictMode doesn't run those twice).
  const setVideo = useCallback((v: HTMLVideoElement | null) => {
    const old = videoRef.current;
    videoRef.current = v;
    if (old && old !== v) {
      old.removeAttribute("src");
      old.load();
    }
  }, []);

  const [paused, setPaused] = useState(false);
  const [firstFrame, setFirstFrame] = useState<string | null>(null);
  const [showFirstFrame, setShowFirstFrame] = useState(false);
  const [snapFirstFrame, setSnapFirstFrame] = useState(false);
  const [fadeMs, setFadeMs] = useState(EASED_FADE); // an eased clip's overlay fade, set with each change (ClipCycle)
  const [eased, setEased] = useState(false); // a clip with its ease rendered in: even fades to and from the still
  const [debugInfo, setDebugInfo] = useState<{ currentTime?: string; playbackRate?: string; duration?: string }>({});
  const [searchParams] = useSearchParams();
  const showDebug = mode === "cycle" && searchParams.has("debug");

  useImperativeHandle(
    ref,
    () => ({
      kind: () => (isVideo ? "video" : "image"),
      clipTime: () => {
        const v = videoRef.current;
        if (!isVideo || !v) return null;
        if (mode === "static") return clipT ?? 0;
        return holdRef.current ? v.currentTime : null;
      },
      ready: () => {
        if (isVideo) {
          const v = videoRef.current;
          if (!v) return Promise.resolve();
          if (mode === "static") return withTimeout(seekClip(v, clipT ?? 0), READY_TIMEOUT_MS * 2);
          return withTimeout(holdDone.current, READY_TIMEOUT_MS);
        }
        const img = imgRef.current;
        return img ? withTimeout(img.decode(), READY_TIMEOUT_MS) : Promise.resolve();
      },
      prime: () => {
        if (isVideo) {
          const v = videoRef.current;
          if (!v) return Promise.resolve();
          return (v.readyState >= 2 ? Promise.resolve() : waitEvent(v, "loadeddata", PRIME_TIMEOUT_MS)).then(twoFrames);
        }
        const img = imgRef.current;
        return img ? withTimeout(img.decode(), PRIME_TIMEOUT_MS).then(twoFrames) : Promise.resolve();
      },
      element: () => (isVideo ? videoRef.current : imgRef.current),
      canPlayNow: () => !!cycleRef.current?.canPlayNow,
      playNow: () => cycleRef.current?.playNow() ?? false,
    }),
    [isVideo, mode, clipT],
  );

  // New base: clear any clip state (as MediaDisplay does).
  useEffect(() => {
    setFirstFrame(null);
    setShowFirstFrame(false);
    setSnapFirstFrame(false);
    setPaused(false);
  }, [src]);

  // Extract the clip's first frame for the end-of-loop cross-fade (as MediaDisplay does). Not while the clip is
  // still fading in: a 4K decode + JPEG encode on the kiosk's CPU waits until it's the one on glass.
  useEffect(() => {
    if (mode !== "cycle" || !isVideo || !src || !shouldPlay) return;
    let cancelled = false;
    const v = document.createElement("video");
    v.crossOrigin = "anonymous";
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.src = src;
    const release = () => {
      v.removeAttribute("src");
      v.load(); // free the decoder (today's version leaves it to GC)
    };
    (async () => {
      await waitEvent(v, "loadedmetadata", 15000);
      if (cancelled) return;
      v.currentTime = 0;
      const t0 = performance.now();
      while ((v.readyState < 2 || v.seeking) && performance.now() - t0 < 15000) {
        await twoFrames();
        if (cancelled) return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx || !canvas.width) return;
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      if (!cancelled) setFirstFrame(canvas.toDataURL("image/jpeg", 0.9));
    })()
      .catch((e) => console.warn("[ArtMedia] first-frame extraction failed", e))
      .finally(release);
    return () => {
      cancelled = true;
    };
  }, [src, mode, isVideo, shouldPlay]);

  // The slow-motion loop.
  useEffect(() => {
    if (mode !== "cycle" || !isVideo || !shouldPlay) return;
    const v = videoRef.current;
    if (!v) return;
    const cycle = new ClipCycle(v, {
      setPaused,
      setShowFirstFrame: (s, ms) => {
        if (s) setSnapFirstFrame(false);
        if (ms) setFadeMs(ms);
        setShowFirstFrame(s);
      },
      snapFirstFrameOff: () => {
        setSnapFirstFrame(true);
        setShowFirstFrame(false);
      },
      log: showDebug ? (...a: unknown[]) => console.log("[ArtMedia]", ...a) : undefined,
    });
    cycleRef.current = cycle;
    if (holdRef.current) holdDone.current = cycle.hold();
    let alive = true;
    void clipInfo(src).then((clip) => {
      if (!alive) return;
      const loop = easedLoop(clip);
      setEased(!!loop);
      cycle.setEased(loop);
      // a video made at real speed loops at real speed, eased in and out by the browser (owner, 20 Nov)
      cycle.setPace(clip?.pace === "natural" ? 1 : PACE);
      cycle.start();
    });
    return () => {
      alive = false;
      cycle.dispose();
      if (cycleRef.current === cycle) cycleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldPlay, videoKey, src, mode]);

  // Hold / release a clip.
  useLayoutEffect(() => {
    if (holdRef.current === hold) return;
    holdRef.current = hold;
    const cycle = cycleRef.current;
    if (hold) {
      if (cycle) holdDone.current = cycle.hold();
      else if (videoRef.current) {
        videoRef.current.pause();
        holdDone.current = Promise.resolve();
      }
    } else {
      cycle?.resume();
    }
  }, [hold]);

  // ?debug: today's time/speed readout.
  useEffect(() => {
    if (!showDebug || !isVideo) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        setDebugInfo({
          currentTime: v.currentTime.toFixed(2),
          playbackRate: v.playbackRate.toFixed(2),
          duration: v.duration.toFixed(2),
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [showDebug, isVideo, videoKey]);

  const media = mediaStyle(displayMode);

  const overlayStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    opacity: paused && showDebug ? 1 : 0,
    transition: "opacity 1000ms ease",
    zIndex: 1,
  };

  // Laid out as the video is (the same size and fit), so the cross-fade target and frame 0 under it match.
  const firstFrameStyle: React.CSSProperties = {
    ...media,
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    opacity: showFirstFrame ? 1 : 0,
    transition: snapFirstFrame
      ? "none"
      : eased
        ? `opacity ${fadeMs}ms ${OPACITY_EASING}`
        : showFirstFrame
          ? `opacity ${CROSSFADE_DURATION}ms ${OPACITY_EASING}`
          : `opacity ${FIRST_FRAME_FADE_OUT_DURATION}ms ${OPACITY_EASING}`,
    zIndex: 2,
  };

  return (
    <div style={mediaContainerStyle}>
      {isVideo ? (
        <video
          key={videoKey}
          ref={setVideo}
          src={src}
          muted
          playsInline
          preload="auto"
          style={media}
        />
      ) : (
        <img ref={imgRef} src={src} style={media} alt="media" />
      )}

      {firstFrame && <img src={firstFrame} alt="first frame" style={firstFrameStyle} />}

      <div style={overlayStyle}>
        <div style={{ color: "white", textAlign: "center", fontSize: "2rem" }}>Paused on Final Frame</div>
      </div>

      {showDebug && (
        <div
          style={{
            position: "fixed",
            bottom: 10,
            left: 10,
            background: "rgba(0,0,0,0.6)",
            color: "white",
            padding: "0.5em 1em",
            borderRadius: 8,
            fontFamily: "monospace",
            fontSize: 14,
            zIndex: 9999,
          }}
        >
          <div>Time: {debugInfo.currentTime}s</div>
          <div>Speed: {debugInfo.playbackRate}x</div>
          <div>Duration: {debugInfo.duration}s</div>
        </div>
      )}
    </div>
  );
});
